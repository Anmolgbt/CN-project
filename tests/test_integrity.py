import hashlib

import pytest

from common.integrity import expected_chunk_length, sha256_bytes, sha256_file, sha256_hex, total_chunks
from tests.conftest import make_bytes


def test_hash_helpers_agree_with_hashlib():
    data = make_bytes(5000)
    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()
    assert sha256_bytes(data) == hashlib.sha256(data).digest()


def test_sha256_file_matches_memory_hash(tmp_path):
    data = make_bytes(3 * 1024 * 1024 + 17)
    p = tmp_path / "f"
    p.write_bytes(data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


def test_empty_file_hash(tmp_path):
    p = tmp_path / "e"
    p.write_bytes(b"")
    assert sha256_file(p) == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("size,chunk,expected", [(0, 10, 0), (1, 10, 1), (10, 10, 1), (11, 10, 2), (100, 10, 10)])
def test_total_chunks(size, chunk, expected):
    assert total_chunks(size, chunk) == expected


def test_expected_chunk_length_last_chunk_is_short():
    assert [expected_chunk_length(25, 10, i) for i in range(3)] == [10, 10, 5]
    with pytest.raises(ValueError):
        expected_chunk_length(25, 10, 3)
    with pytest.raises(ValueError):
        total_chunks(5, 0)
