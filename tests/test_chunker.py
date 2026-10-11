import hashlib

import pytest

from client.chunker import read_chunk, scan_file
from tests.conftest import make_bytes


def scan(tmp_path, size, chunk):
    p = tmp_path / "f.bin"
    data = make_bytes(size)
    p.write_bytes(data)
    return data, scan_file(p, chunk)


def test_empty_file_has_zero_chunks(tmp_path):
    data, m = scan(tmp_path, 0, 100)
    assert m.chunks == [] and m.size == 0 and m.sha256 == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("size,chunk,count,last", [
    (1, 100, 1, 1),            # tiny file
    (99, 100, 1, 99),          # smaller than a chunk
    (100, 100, 1, 100),        # exactly one chunk
    (101, 100, 2, 1),          # partial final chunk
    (1000, 100, 10, 100),      # 10 equal chunks (the "10 MB / 1 MB" example, scaled down)
])
def test_chunk_geometry(tmp_path, size, chunk, count, last):
    data, m = scan(tmp_path, size, chunk)
    assert len(m.chunks) == count
    assert m.chunks[-1].length == last
    assert [c.chunk_id for c in m.chunks] == list(range(count))
    assert [c.offset for c in m.chunks] == [i * chunk for i in range(count)]
    assert sum(c.length for c in m.chunks) == size


def test_chunk_hashes_and_reassembly_in_any_order(tmp_path):
    data, m = scan(tmp_path, 1050, 100)
    p = tmp_path / "f.bin"
    parts = {}
    with open(p, "rb") as fh:
        for c in reversed(m.chunks):          # read in reverse order
            piece = read_chunk(fh, c)
            assert hashlib.sha256(piece).hexdigest() == c.sha256
            parts[c.offset] = piece
    assert b"".join(parts[k] for k in sorted(parts)) == data
    assert m.sha256 == hashlib.sha256(data).hexdigest()


def test_invalid_chunk_size(tmp_path):
    p = tmp_path / "x"
    p.write_bytes(b"abc")
    with pytest.raises(ValueError):
        scan_file(p, 0)
