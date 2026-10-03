import pytest
import s3_put_retry as sp


class FlakyS3:
    "Fails the first ``fail`` upload_part calls, as the proxy does with a 520."

    def __init__(self, fail: int):
        self.fail, self.calls = fail, 0

    def upload_part(self, **kw):
        self.calls += 1
        if self.calls <= self.fail:
            raise RuntimeError("520")
        return {"ETag": f'"etag-{kw["PartNumber"]}"'}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)


def test_a_failed_part_is_retried_and_reads_its_own_slice(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PART", 4)
    f = tmp_path / "x.bin"
    f.write_bytes(b"aaaabbbbcc")
    s3 = FlakyS3(fail=2)
    seen = []
    orig = s3.upload_part
    s3.upload_part = lambda **kw: (seen.append(kw["Body"]), orig(**kw))[1]
    assert sp.put_part(s3, "b", "k", "u", f, 2) == {"PartNumber": 2, "ETag": '"etag-2"'}
    assert s3.calls == 3
    assert seen == [b"bbbb"] * 3


def test_gives_up_after_the_attempt_limit(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"data")
    s3 = FlakyS3(fail=10**6)
    with pytest.raises(RuntimeError, match="520"):
        sp.put_part(s3, "b", "k", "u", f, 1)
    assert s3.calls == sp.ATTEMPTS
