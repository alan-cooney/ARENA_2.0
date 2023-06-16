# %%
import torch
from torch import distributed as dist
from torch.distributed import ReduceOp
from typing import List

import threading

# %%
from test import test_broadcast_naive

def broadcast_naive(tensor: torch.Tensor, src: int):
    if dist.get_rank() == src:
        for i in range(dist.get_world_size()):
            if i != dist.get_rank():
                dist.send(tensor, i)
    else:
        dist.recv(tensor, src)


if __name__ == '__main__':
    test_broadcast_naive(broadcast_naive)
#%%
from test import test_broadcast_tree

def broadcast_tree(tensor: torch.Tensor, src: int):
    curr_mult = 1
    rank_shifted = lambda: (dist.get_rank() - src) % dist.get_world_size()
    while curr_mult * 2 <= dist.get_world_size():
        if rank_shifted() < curr_mult:
            dist.send(tensor, (dist.get_rank() + curr_mult) % dist.get_world_size())
        elif rank_shifted() < curr_mult * 2:
            dist.recv(tensor, (dist.get_rank() - curr_mult) % dist.get_world_size())
        curr_mult *= 2
        dist.barrier()

if __name__ == '__main__':
    test_broadcast_tree(broadcast_tree)

#%%
from test import test_broadcast_ring

def broadcast_ring(tensor: torch.Tensor, src: int):
    to_shifted = lambda i: (i - src) % dist.get_world_size()
    to_orig = lambda i: (i + src) % dist.get_world_size()
    for i in range(1, dist.get_world_size()):
        if to_shifted(dist.get_rank()) == i-1:
            print(f'{dist.get_rank()} | {to_shifted(dist.get_rank())} -> {to_orig(i)}')
            dist.send(tensor, to_orig(i))
        elif to_shifted(dist.get_rank()) == i:
            dist.recv(tensor, to_orig(i-1))
        dist.barrier()

if __name__ == '__main__':
    test_broadcast_ring(broadcast_ring)

# %%
from test import test_reduce_naive

def reduce_naive(tensor: torch.Tensor, dst: int, op=ReduceOp.SUM):
    if dist.get_rank() == dst:
        for i in range(dist.get_world_size()):
            if i != dist.get_rank():
                buff = torch.empty_like(tensor)
                dist.recv(buff, i)
                dist.barrier()
                if op == ReduceOp.SUM:
                    tensor += buff
                elif op == ReduceOp.PRODUCT:
                    tensor *= buff
                elif op == ReduceOp.MAX:
                    tensor = torch.max(tensor, buff)
                elif op == ReduceOp.MIN:
                    tensor = torch.min(tensor, buff)
                else:
                    raise NotImplementedError(f'op {op} not implemented')
    else:
        for i in range(dist.get_world_size()):
            if i == dist.get_rank():
                dist.send(tensor, dst)
            elif i == dst:
                continue
            dist.barrier()
    dist.barrier()

if __name__ == '__main__':
    test_reduce_naive(reduce_naive)

# %%
from test import test_reduce_tree

def reduce_tree(tensor: torch.Tensor, dst: int, op=ReduceOp.SUM):
    curr_mult = dist.get_world_size() / 2
    rank_shifted = lambda: (dist.get_rank() - dst) % dist.get_world_size()
    while curr_mult >= 1:
        if rank_shifted() < curr_mult:
            buff = torch.empty_like(tensor)
            dist.recv(buff, (dist.get_rank() + curr_mult) % dist.get_world_size())
            if op == ReduceOp.SUM:
                tensor += buff
            elif op == ReduceOp.PRODUCT:
                tensor *= buff
            elif op == ReduceOp.MAX:
                tensor = torch.max(tensor, buff)
            elif op == ReduceOp.MIN:
                tensor = torch.min(tensor, buff)
            else:
                raise NotImplementedError(f'op {op} not implemented')
        elif rank_shifted() < curr_mult * 2:
            dist.send(tensor, (dist.get_rank() - curr_mult) % dist.get_world_size())
        curr_mult /= 2
    dist.barrier()

if __name__ == '__main__':
    test_reduce_tree(reduce_tree)

#%%
from test import test_allreduce_naive

def allreduce_naive(tensor: torch.Tensor, op=ReduceOp.SUM):
    reduce_naive(tensor, dst=0, op=op)
    broadcast_naive(tensor, src=0)

if __name__ == '__main__':
    test_allreduce_naive(allreduce_naive)

#%%
from test import test_allreduce_butterfly

def allreduce_butterfly(tensor: torch.Tensor, op=ReduceOp.SUM):
    rank = bin(dist.get_rank())[2:].zfill(len(bin(dist.get_world_size()-1)[2:]))
    buff = torch.empty_like(tensor)
    for i in range(len(rank)):
        partner_rank = rank[:i] + str(1-int(rank[i])) + rank[i+1:]
        partner_rank = int(partner_rank, 2)
        dist.send(tensor.clone(), partner_rank)
        dist.recv(buff, partner_rank)
        if op == ReduceOp.SUM:
            tensor += buff
        elif op == ReduceOp.PRODUCT:
            tensor *= buff
        elif op == ReduceOp.MAX:
            tensor = torch.max(tensor, buff)
        elif op == ReduceOp.MIN:
            tensor = torch.min(tensor, buff)
        else:
            raise NotImplementedError(f'op {op} not implemented')
    dist.barrier()

if __name__ == '__main__':
    test_allreduce_butterfly(allreduce_butterfly)

# %%
from test import test_gather_tree

def gather_tree(tensor: torch.Tensor, dst: int):
    def indir_ranks(curr_mult: int) -> List[int]:
        indir_rank = 0
        out = [indir_rank]
        indir_mult = curr_mult
        while indir_rank + indir_mult < dist.get_world_size():
            indir_rank += indir_mult
            out.append(int(indir_rank))
            indir_mult *= 2
        return out
    # TODO: Have changed code for tmp_buff to receive full buffer from src rank, need to debug extraction of indices
    # and sending to identify deadlock
    curr_mult = dist.get_world_size() // 2
    rank_shifted = lambda: (dist.get_rank() - dst) % dist.get_world_size()
    buff = torch.zeros([tensor.shape[0] * dist.get_world_size()] + list(tensor.shape[1:]))
    b_idx = lambda x: int(tensor.shape[0] * x)
    buff[b_idx(dist.get_rank()):b_idx(dist.get_rank()+1)] = tensor
    while curr_mult >= 1:
        if rank_shifted() < curr_mult:
            print(f'rank {dist.get_rank()} receiving, curr_mult {curr_mult}, addns {indir_ranks(curr_mult)}')
            src_rank = int((dist.get_rank()+curr_mult)%dist.get_world_size())
            tmp_buff = torch.empty_like(buff)
            dist.recv(tmp_buff, src_rank)
            for r in indir_ranks(curr_mult):
                data_src_rank = (src_rank+r)%dist.get_world_size()
                print(f'rank {src_rank} to {dist.get_rank()} - recv idx {b_idx(data_src_rank)}:{b_idx(data_src_rank+1)}')
                buff[b_idx(data_src_rank):b_idx(data_src_rank+1)] = tmp_buff[b_idx(data_src_rank):b_idx(data_src_rank+1)]
                print(f'rank {src_rank} to {dist.get_rank()} - DONE recv idx {b_idx(data_src_rank)}:{b_idx(data_src_rank+1)} - {buff}')
        elif rank_shifted() < curr_mult * 2:
            print(f'rank {dist.get_rank()} sending')
            for r in indir_ranks(curr_mult):
                dst_rank = int((dist.get_rank()-curr_mult)%dist.get_world_size())
                dist.send(buff, dst_rank)
        curr_mult //= 2
    dist.barrier()

if __name__ == '__main__':
    test_gather_tree(gather_tree)
# %%