from datetime import date, timedelta

from app.services.allocator import DaySlot, PendingItem, TaskInfo, allocate

MON = date(2026, 11, 2)


def week(caps, weekend_idx=(6,)):
    return [DaySlot(MON + timedelta(days=i), c, i in weekend_idx) for i, c in enumerate(caps)]


def by_day(plan):
    return {d: [p.task_id for p in ps] for d, ps in plan.items()}


def test_capacity_respected_and_tasks_not_split():
    tasks = [TaskInfo(1, 60, 3), TaskInfo(2, 30, 5), TaskInfo(3, 45, 2)]
    days = week([90, 60, 120, 0, 90, 30, 60])
    plan, _ = allocate(days, tasks, {}, set(), [])
    dur = {t.id: t.duration for t in tasks}
    for d in days:
        assert sum(dur[t] for t in by_day(plan)[d.date]) <= d.capacity


def test_weekly_counts_never_exceeded_and_one_per_day():
    tasks = [TaskInfo(1, 30, 3), TaskInfo(2, 30, 2)]
    plan, remaining = allocate(week([200] * 7), tasks, {}, set(), [])
    flat = [t for ids in by_day(plan).values() for t in ids]
    assert flat.count(1) == 3 and flat.count(2) == 2
    assert remaining == {1: 0, 2: 0}
    for ids in by_day(plan).values():
        assert len(ids) == len(set(ids))


def test_avoids_consecutive_days_when_possible():
    tasks = [TaskInfo(1, 60, 3)]
    plan, _ = allocate(week([120] * 7), tasks, {}, set(), [])
    days_with = [i for i, d in enumerate(sorted(plan)) if 1 in by_day(plan)[d]]
    assert len(days_with) == 3
    assert all(b - a >= 2 for a, b in zip(days_with, days_with[1:]))


def test_consecutive_allowed_when_unavoidable():
    tasks = [TaskInfo(1, 30, 7)]
    plan, remaining = allocate(week([60] * 7), tasks, {}, set(), [])
    assert remaining[1] == 0


def test_previous_day_counts_for_consecutive_rule():
    tasks = [TaskInfo(1, 30, 2), TaskInfo(2, 30, 2)]
    plan, _ = allocate(week([30] * 7), tasks, {}, prev_day_task_ids={1}, pending=[])
    assert by_day(plan)[MON] == [2]


def test_least_assigned_first_round_robin():
    tasks = [TaskInfo(1, 60, 2), TaskInfo(2, 60, 2), TaskInfo(3, 60, 2)]
    plan, _ = allocate(week([60] * 6 + [0]), tasks, {}, set(), [])
    order = [by_day(plan)[d][0] for d in sorted(plan) if by_day(plan)[d]]
    assert sorted(order[:3]) == [1, 2, 3]  # 첫 세 번에 세 과제가 한 번씩


def test_task_that_does_not_fit_moves_to_another_day():
    tasks = [TaskInfo(1, 90, 1)]
    plan, remaining = allocate(week([60, 60, 120, 0, 0, 0, 0]), tasks, {}, set(), [])
    assert by_day(plan)[MON + timedelta(days=2)] == [1]
    assert remaining[1] == 0


def test_weekend_places_pending_first_then_regular():
    tasks = [TaskInfo(1, 60, 1), TaskInfo(2, 30, 1)]
    days = [DaySlot(MON + timedelta(days=6), 90, True)]
    pend = [PendingItem(assignment_id=99, task_id=1, duration=60)]
    plan, _ = allocate(days, tasks, {1: 1}, set(), pend)
    ps = plan[days[0].date]
    assert (ps[0].kind, ps[0].task_id, ps[0].makeup_of_id) == ("makeup", 1, 99)
    assert (ps[1].kind, ps[1].task_id) == ("regular", 2)


def test_weekday_never_gets_pending():
    days = [DaySlot(MON, 120, False)]
    plan, _ = allocate(days, [TaskInfo(1, 60, 1)], {1: 1}, set(), [PendingItem(5, 1, 60)])
    assert plan[MON] == []


def test_unassigned_count_reported():
    plan, remaining = allocate(week([30] * 7), [TaskInfo(1, 60, 2)], {}, set(), [])
    assert remaining[1] == 2


def test_soft_consecutive_rule_uses_spare_time_instead_of_dropping():
    # 실제 화면에서 발견한 케이스: 연속 회피 때문에 화요일 여유 시간을 못 쓰고 1회 미배정되던 문제
    tasks = [TaskInfo(1, 60, 3, "R"), TaskInfo(2, 30, 5, "V"), TaskInfo(3, 45, 2, "W"), TaskInfo(4, 20, 3, "S")]
    days = week([120, 120, 60, 0, 180, 180, 60], weekend_idx=(4, 5, 6))
    plan, remaining = allocate(days, tasks, {}, set(), [])
    assert all(n == 0 for n in remaining.values())
    dur = {t.id: t.duration for t in tasks}
    for d in days:
        ids = by_day(plan)[d.date]
        assert sum(dur[t] for t in ids) <= d.capacity
        assert len(ids) == len(set(ids))


def test_consecutive_allowed_when_no_slack_left():
    # 어제 했던 과제라도, 오늘 빼면 남은 날이 딱 맞거나 부족하면 오늘 배정
    tasks = [TaskInfo(1, 30, 5), TaskInfo(2, 60, 2)]
    days = week([90, 120, 60, 0, 180, 180, 60], weekend_idx=(4, 5, 6))
    plan, remaining = allocate(days, tasks, {}, prev_day_task_ids={1}, pending=[])
    assert 1 in by_day(plan)[MON]
    assert remaining == {1: 0, 2: 0}
