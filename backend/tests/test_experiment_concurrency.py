"""Two independent transactions converge without duplicate roots or promoted versions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.experiments.models import ExperimentRow, ExperimentVersionRow
from app.experiments.service import ExperimentService
from app.schemas.experiments import ExperimentCreate, ExperimentPromotion
from tests.support.experiment_fixtures import World
from tests.support.experiment_fixtures import experiment_engine as _experiment_engine  # noqa: F401


def test_concurrent_creation_and_promotion_converge(experiment_engine):
    with Session(experiment_engine, expire_on_commit=False) as session:
        w = World(session)
        body = ExperimentCreate(
            name="Concurrent", idempotency_key="same-concurrent", configuration=w.configuration()
        )
        tenant, settings = w.tenant, w.settings
        now = w.now
        session.commit()
    barrier = Barrier(2, timeout=5)

    def create():
        with Session(experiment_engine) as session:
            service = ExperimentService(session, settings, clock=lambda: now)
            barrier.wait()
            v = service.create(tenant, body)
            session.commit()
            return v

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        a, b = [future.result(timeout=10) for future in futures]
    assert a.id == b.id
    with Session(experiment_engine, expire_on_commit=False) as session:
        w.session = session
        w.service = ExperimentService(
            session, settings, clock=lambda: w.now, source_resolver=w.resolver
        )
        v = w.transition(w.approve(w.transition(a, "submit")), "start")
        w.sample(v)
        v = w.transition(v, "complete")
        now = w.now
        session.commit()
    barrier = Barrier(2, timeout=5)
    promotion = ExperimentPromotion(expected_revision=v.revision, variant_key="baseline")

    def promote():
        with Session(experiment_engine) as session:
            service = ExperimentService(session, settings, clock=lambda: now)
            barrier.wait()
            child = service.promote(tenant, v.experiment_id, v.id, promotion)
            session.commit()
            return child

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(promote) for _ in range(2)]
        children = [future.result(timeout=10) for future in futures]
    assert children[0].id == children[1].id
    assert children[0].sample_counts == {"baseline": 0}
    with Session(experiment_engine) as session:
        assert session.scalar(select(func.count()).select_from(ExperimentRow)) == 1
        assert session.scalar(select(func.count()).select_from(ExperimentVersionRow)) == 2
