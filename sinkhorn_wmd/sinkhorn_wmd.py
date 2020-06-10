import numpy
import scipy.sparse
from scipy.spatial.distance import cdist
import os
import sys
import torch

sys.path.append(os.path.join(os.path.dirname(__file__), os.pardir))
from utils import parse_model_args, train, inference, save_model  # noqa

# Kernel parameters.
N_DOCS = 5000
QUERY_IDX = 100
LAMBDA = 1

# Data files. (Ask Adrian for these.)
DATA_MAT = 'data/cache-mat.npz'
DATA_VECS = 'data/cache-vecs.npy'


def swmd_numpy(r, c, vecs, niters):
    # I=(r > 0)
    sel = r.squeeze() > 0

    # r=r(I)
    r = r[sel].reshape(-1, 1).astype(numpy.float64)

    # M=M(I,:)
    M = cdist(vecs[sel], vecs).astype(numpy.float64)

    # x=ones(length(r), size(c,2)) / length(r)
    a_dim = r.shape[0]
    b_nobs = c.shape[1]
    x = numpy.ones((a_dim, b_nobs)) / a_dim

    # K=exp(-lambda * M)
    K = numpy.exp(- M * LAMBDA)
    K_div_r = K / r
    K_T = K.T

    # This version uses a fixed number of iterations instead of running
    # until convergence.
    for it in range(niters):
        print('starting iteration {}'.format(it))

        u = 1.0 / x

        # Here's where a better implementation is possible by doing the
        # SDDMM thing and avoiding the dense matrix/matrix multiply. We do the
        # slow thing for now.
        K_T_times_u = K_T @ u
        one_over_K_T_times_u = 1 / (K_T_times_u)
        v = c.multiply(one_over_K_T_times_u)

        x = K_div_r @ v.tocsc()

    out = (u * ((K * M) @ v)).sum(axis=0)
    return out


def _sparse_sample(indices, tensor):
    """Get the values of a dense `tensor` at the given `indices`
    (specified the same way as a sparse PyTorch tensor).
    """
    length = indices.shape[1]
    values = torch.Tensor(length)
    for i in range(length):
        coord = tuple(indices[:, i].tolist())
        values[i] = tensor[coord]
    return torch.sparse.FloatTensor(
        indices,
        values,
        tensor.shape,
    )


def _sdmp(a, b):
    """Sparse/dense matrix product.
    """
    out = torch.Tensor(torch.Size((a.shape[0], b.shape[1])))
    for i in range(out.shape[0]):
        for j in range(out.shape[1]):
            for k in range(a.shape[1]):
                out[i, j] = a[i, k] * b[k, j]


def swmd_torch(r, c, vecs, niters):
    # Convert arrays to PyTorch tensors.
    r = torch.FloatTensor(r)
    c_coo = c.tocoo()
    c = torch.sparse.FloatTensor(
        torch.LongTensor(numpy.vstack((c_coo.row, c_coo.col))),
        torch.FloatTensor(c_coo.data),
        torch.Size(c_coo.shape),
    )
    vecs = torch.FloatTensor(vecs)

    # I=(r > 0)
    sel = r > 0

    # r=r(I)
    r = r[sel].reshape(-1, 1)

    # M=M(I,:)
    M = torch.cdist(vecs[sel], vecs)

    # x=ones(length(r), size(c,2)) / length(r)
    a_dim = r.shape[0]
    b_nobs = c.shape[1]
    x = torch.ones((a_dim, b_nobs)) / a_dim

    # K=exp(-lambda * M)
    K = torch.exp(- M * LAMBDA)
    K_div_r = K / r
    K_T = K.T

    for it in range(niters):
        print('starting iteration {}'.format(it))

        u = 1.0 / x

        K_T_times_u = K_T @ u
        one_over_K_T_times_u = 1 / (K_T_times_u)

        # PyTorch doesn't support elementwise multiply between sparse
        # and dense matrices, so we have to convert one operand to
        # sparse first.
        v = c * _sparse_sample(c._indices(), one_over_K_T_times_u)

        # PyTorch doesn't support dense/sparse matrix multiply (only
        # sparse/dense), so I had to write my own. :'(
        x = _sdmp(K_div_r, v)

    out = (u * ((K * M) @ v)).sum(axis=0)
    return out


def add_args(parser):
    parser.add_argument('-n', '--niters', default=16, type=int,
                        help="number of iterations")
    parser.add_argument('-d', '--dump', default=False, action='store_true',
                        help="dump result to a file")
    parser.add_argument('-p', '--numpy', default=False, action='store_true',
                        help="use NumPy version instead of PyTorch")


if __name__ == "__main__":
    args = parse_model_args(add_args)

    # Load data.
    vecs = numpy.load(DATA_VECS)
    mat = scipy.sparse.load_npz(DATA_MAT)
    mat = mat[:, :N_DOCS]  # Use a subset of the data.
    print('data loaded')

    # The query vector.
    r = numpy.asarray(mat[:, QUERY_IDX].todense()).squeeze()

    # The kernel itself.
    kernel = swmd_numpy if args.numpy else swmd_torch
    scores = kernel(r, mat, vecs,
                    niters=args.niters)

    # Dump output.
    if args.dump:
        numpy.savetxt('scores_out.txt', scores, fmt='%.8e')