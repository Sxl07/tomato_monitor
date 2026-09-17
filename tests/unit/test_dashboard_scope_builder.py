"""Tests for dashboard_scope_builder (Spec 024, Block 9).

Covers greenhouse/module resolution, the mandatory candidate->metrics->valid->
results order, pct_* fallback (useful-only), and — critically — multiuser
isolation: ids from a foreign greenhouse/module must never reach the bulk repos.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.application.services import dashboard_scope_builder as builder


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _gh(gid, name="GH"):
    return SimpleNamespace(id=gid, name=f"{name}{gid}")


def _mod(mid, gh_id, name="M"):
    return SimpleNamespace(id=mid, greenhouse_id=gh_id, name=f"{name}{mid}")


def _mon(mid, day=1, status="completed"):
    return SimpleNamespace(
        id=mid, status=status, started_at=datetime(2025, 6, day)
    )


def _metrics(mid, total=10, healthy=8, unhealthy=2, **pcts):
    base = dict(
        pct_green=0.0, pct_breaker=0.0, pct_turning=0.0,
        pct_pink=0.0, pct_light_red=0.0, pct_red=0.0,
    )
    base.update(pcts)
    return SimpleNamespace(
        monitoring_id=mid,
        total_tomatoes=total,
        healthy_count=healthy,
        unhealthy_count=unhealthy,
        **base,
    )


class FakeModuleRepo:
    def __init__(self, modules_by_gh):
        self._by_gh = modules_by_gh
        self.calls = []

    def get_by_greenhouse(self, gh_id):
        self.calls.append(gh_id)
        return list(self._by_gh.get(gh_id, []))


class FakeMonitoringRepo:
    def __init__(self, mons_by_module):
        self._by_module = mons_by_module
        self.calls = []

    def get_by_module(self, module_id):
        self.calls.append(module_id)
        return list(self._by_module.get(module_id, []))


class FakeMetricsRepo:
    def __init__(self, metrics_by_id):
        self._by_id = metrics_by_id
        self.requested_ids = []

    def get_by_monitoring_ids(self, ids):
        self.requested_ids.append(list(ids))
        return {i: self._by_id[i] for i in ids if i in self._by_id}


class FakeInspectionRepo:
    def __init__(self, results_by_id):
        self._by_id = results_by_id
        self.requested_ids = []

    def get_by_monitoring_ids(self, ids):
        self.requested_ids.append(list(ids))
        return {i: list(self._by_id[i]) for i in ids if i in self._by_id}


def _fake_result(stage):
    return SimpleNamespace(maturity_stage=stage)


# ---------------------------------------------------------------------------
# Greenhouse / module resolution
# ---------------------------------------------------------------------------


class TestResolveGreenhouse:
    def test_none_when_no_greenhouses(self):
        assert builder.resolve_selected_greenhouse([], "1") is None

    def test_param_selects_owned(self):
        ghs = [_gh(3), _gh(7)]
        assert builder.resolve_selected_greenhouse(ghs, "7").id == 7

    def test_default_min_id_when_missing(self):
        ghs = [_gh(7), _gh(3)]
        assert builder.resolve_selected_greenhouse(ghs, None).id == 3

    def test_foreign_param_falls_back_to_min(self):
        ghs = [_gh(7), _gh(3)]
        # 99 belongs to another user -> not in owned set -> min id
        assert builder.resolve_selected_greenhouse(ghs, "99").id == 3

    def test_invalid_param_falls_back_to_min(self):
        ghs = [_gh(7), _gh(3)]
        assert builder.resolve_selected_greenhouse(ghs, "abc").id == 3


class TestResolveModuleScope:
    def test_all_when_missing(self):
        assert builder.resolve_module_scope([_mod(1, 1)], None) == ("all", None)

    def test_all_keyword(self):
        assert builder.resolve_module_scope([_mod(1, 1)], "all") == ("all", None)

    def test_owned_module(self):
        assert builder.resolve_module_scope([_mod(5, 1)], "5") == ("module", 5)

    def test_foreign_module_falls_back_to_all(self):
        assert builder.resolve_module_scope([_mod(5, 1)], "999") == ("all", None)

    def test_invalid_module_falls_back_to_all(self):
        assert builder.resolve_module_scope([_mod(5, 1)], "xx") == ("all", None)


# ---------------------------------------------------------------------------
# build_scope_data
# ---------------------------------------------------------------------------


class TestBuildScopeData:
    def test_none_when_no_greenhouses(self):
        result = builder.build_scope_data(
            greenhouses=[],
            module_repo=FakeModuleRepo({}),
            monitoring_repo=FakeMonitoringRepo({}),
            metrics_repo=FakeMetricsRepo({}),
            inspection_repo=FakeInspectionRepo({}),
            greenhouse_id_param=None,
            module_id_param=None,
        )
        assert result is None

    def test_valid_requires_metrics(self):
        gh = _gh(1)
        mod = _mod(10, 1)
        # two candidates; only id 100 has metrics -> only it is valid
        mon_repo = FakeMonitoringRepo({10: [_mon(100, 1), _mon(101, 2)]})
        metrics_repo = FakeMetricsRepo({100: _metrics(100)})
        insp_repo = FakeInspectionRepo({})
        scope = builder.build_scope_data(
            greenhouses=[gh],
            module_repo=FakeModuleRepo({1: [mod]}),
            monitoring_repo=mon_repo,
            metrics_repo=metrics_repo,
            inspection_repo=insp_repo,
            greenhouse_id_param="1",
            module_id_param="all",
        )
        valids = scope.modules[0].valid_monitorings
        assert [v.monitoring_id for v in valids] == [100]
        # bulk metrics requested BOTH candidates; inspection only the valid one
        assert metrics_repo.requested_ids == [[100, 101]]
        assert insp_repo.requested_ids == [[100]]

    def test_real_counts_when_results_present(self):
        gh = _gh(1)
        mod = _mod(10, 1)
        scope = builder.build_scope_data(
            greenhouses=[gh],
            module_repo=FakeModuleRepo({1: [mod]}),
            monitoring_repo=FakeMonitoringRepo({10: [_mon(100)]}),
            metrics_repo=FakeMetricsRepo({100: _metrics(100)}),
            inspection_repo=FakeInspectionRepo({100: [_fake_result("red"), _fake_result("green")]}),
            greenhouse_id_param="1",
            module_id_param="all",
        )
        v = scope.modules[0].valid_monitorings[0]
        assert v.coverage_known is True
        assert v.maturity_counts.covered == 2

    def test_fallback_when_no_results_but_useful_pcts(self):
        gh = _gh(1)
        mod = _mod(10, 1)
        scope = builder.build_scope_data(
            greenhouses=[gh],
            module_repo=FakeModuleRepo({1: [mod]}),
            monitoring_repo=FakeMonitoringRepo({10: [_mon(100)]}),
            metrics_repo=FakeMetricsRepo({100: _metrics(100, pct_red=100.0)}),
            inspection_repo=FakeInspectionRepo({}),  # no rows
            greenhouse_id_param="1",
            module_id_param="all",
        )
        v = scope.modules[0].valid_monitorings[0]
        assert v.coverage_known is False
        assert v.maturity_fallback is not None
        assert v.maturity_fallback.pct_red == 100.0

    def test_no_maturity_when_no_results_and_all_pcts_zero(self):
        gh = _gh(1)
        mod = _mod(10, 1)
        scope = builder.build_scope_data(
            greenhouses=[gh],
            module_repo=FakeModuleRepo({1: [mod]}),
            monitoring_repo=FakeMonitoringRepo({10: [_mon(100)]}),
            metrics_repo=FakeMetricsRepo({100: _metrics(100)}),  # all pct 0
            inspection_repo=FakeInspectionRepo({}),
            greenhouse_id_param="1",
            module_id_param="all",
        )
        v = scope.modules[0].valid_monitorings[0]
        assert v.maturity_counts is None
        assert v.maturity_fallback is None

    def test_non_completed_and_no_startedat_excluded(self):
        gh = _gh(1)
        mod = _mod(10, 1)
        mons = [
            _mon(100, 1, status="completed"),
            _mon(101, 2, status="analyzing"),  # not completed
            SimpleNamespace(id=102, status="completed", started_at=None),  # no date
        ]
        metrics_repo = FakeMetricsRepo({100: _metrics(100), 101: _metrics(101), 102: _metrics(102)})
        scope = builder.build_scope_data(
            greenhouses=[gh],
            module_repo=FakeModuleRepo({1: [mod]}),
            monitoring_repo=FakeMonitoringRepo({10: mons}),
            metrics_repo=metrics_repo,
            inspection_repo=FakeInspectionRepo({}),
            greenhouse_id_param="1",
            module_id_param="all",
        )
        # only 100 is a candidate -> metrics requested only for [100]
        assert metrics_repo.requested_ids == [[100]]
        assert [v.monitoring_id for v in scope.modules[0].valid_monitorings] == [100]


class TestMultiuserIsolation:
    """Foreign ids must never reach the bulk repos."""

    def _repos(self):
        # User owns greenhouse 1 with module 10 (monitoring 100).
        # Greenhouse 2 / module 20 / monitoring 200 belong to ANOTHER user and
        # are NOT in get_all_by_owner output.
        module_repo = FakeModuleRepo({1: [_mod(10, 1)]})
        monitoring_repo = FakeMonitoringRepo({10: [_mon(100)], 20: [_mon(200)]})
        metrics_repo = FakeMetricsRepo({100: _metrics(100), 200: _metrics(200)})
        insp_repo = FakeInspectionRepo({100: [_fake_result("red")], 200: [_fake_result("red")]})
        return module_repo, monitoring_repo, metrics_repo, insp_repo

    def test_foreign_greenhouse_id_ignored(self):
        module_repo, monitoring_repo, metrics_repo, insp_repo = self._repos()
        scope = builder.build_scope_data(
            greenhouses=[_gh(1)],  # user owns only gh 1
            module_repo=module_repo,
            monitoring_repo=monitoring_repo,
            metrics_repo=metrics_repo,
            inspection_repo=insp_repo,
            greenhouse_id_param="2",  # another user's greenhouse
            module_id_param="all",
        )
        # Falls back to owned gh 1; only module 10 queried, never gh 2 / module 20.
        assert scope.greenhouse_id == 1
        assert monitoring_repo.calls == [10]
        assert 20 not in monitoring_repo.calls
        # bulk repos never receive foreign monitoring 200
        flat_metrics = [i for call in metrics_repo.requested_ids for i in call]
        assert 200 not in flat_metrics
        flat_insp = [i for call in insp_repo.requested_ids for i in call]
        assert 200 not in flat_insp

    def test_foreign_module_id_falls_back_to_all_owned(self):
        module_repo, monitoring_repo, metrics_repo, insp_repo = self._repos()
        scope = builder.build_scope_data(
            greenhouses=[_gh(1)],
            module_repo=module_repo,
            monitoring_repo=monitoring_repo,
            metrics_repo=metrics_repo,
            inspection_repo=insp_repo,
            greenhouse_id_param="1",
            module_id_param="20",  # module of another greenhouse/user
        )
        # foreign module -> scope "all" over owned modules only (module 10)
        assert scope.scope_kind == "all"
        assert [m.module_id for m in scope.modules] == [10]
        assert monitoring_repo.calls == [10]
        flat_metrics = [i for call in metrics_repo.requested_ids for i in call]
        assert flat_metrics == [100]
        assert 200 not in flat_metrics
