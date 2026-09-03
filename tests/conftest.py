import pytest

from duckmove.core.engine import Engine


@pytest.fixture()
def engine():
    eng = Engine(db_path=":memory:")
    yield eng
    eng.close()
