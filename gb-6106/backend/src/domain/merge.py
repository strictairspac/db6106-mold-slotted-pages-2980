"""交班合单：把白班、夜班两张已完工压模变更单重放进同一槽页。

规则：
- 各单办成的录入、补记、撤销按原顺序参与合单，整理与未办成的操作不带入。
- 同一履历两边都动过，按取舍结果只保留选中一边对该履历的操作。
- 存在能全部写进原容量的交错顺序就全写入；怎么排都装不下就指出卡住的履历。
"""
from collections import deque

from .page import HEADER_BYTES, SLOT_BYTES, SlottedPage

SIDES = ("a", "b")
SIDE_LABELS = {"a": "白班", "b": "夜班"}
ACTIONS = {"insert": "录入", "delete": "撤销", "update": "补记"}


def successful_ops(payload, parameter):
    """重放一张变更单，返回办成的录入、补记、撤销，保持原顺序。"""
    page = SlottedPage(parameter)
    kept = []
    for step, operation in enumerate(payload["operations"], 1):
        action = operation["op"]
        try:
            if action == "compact":
                page.compact()
                continue
            if action == "delete":
                page.delete(operation["key"])
            elif action == "insert":
                page.insert(operation["key"], operation["note"].encode())
            elif action == "update":
                page.update(operation["key"], operation["note"].encode())
            else:
                continue
        except ValueError:
            continue
        kept.append({
            "op": action,
            "key": operation["key"],
            "note": operation.get("note"),
            "step": step,
        })
    return kept


def conflict_keys(ops_a, ops_b):
    """两边都动过的履历。"""
    touched_a = {op["key"] for op in ops_a}
    touched_b = {op["key"] for op in ops_b}
    return sorted(touched_a & touched_b)


def apply_choices(ops_a, ops_b, choices):
    """按取舍去掉落败一边对冲突履历的操作，未冲突的履历不受影响。"""
    def keep(ops, side):
        return [op for op in ops if choices.get(op["key"], side) == side]

    return keep(ops_a, "a"), keep(ops_b, "b")


def _profile(ops):
    """逐步给出：该步所需空闲字节、以及每步之后的槽位数与活记录字节。

    录入固定增加一条槽；撤销保留槽项；补记原地或迁移但槽数不变。
    这些量只取决于本侧已执行的步数，与交错顺序无关。
    """
    needs = []
    slots = [0]
    live = [0]
    lengths = {}
    for op in ops:
        action = op["op"]
        if action == "insert":
            size = len(op["note"].encode())
            needs.append(SLOT_BYTES + size)
            slots.append(slots[-1] + 1)
            live.append(live[-1] + size)
            lengths[op["key"]] = size
        elif action == "delete":
            needs.append(0)
            slots.append(slots[-1])
            live.append(live[-1] - lengths.pop(op["key"]))
        else:
            size = len(op["note"].encode())
            old = lengths[op["key"]]
            needs.append(max(0, size - old))
            slots.append(slots[-1])
            live.append(live[-1] + size - old)
            lengths[op["key"]] = size
    return needs, slots, live


def _search(ops_a, ops_b, parameter):
    """在 (i, j) 网格上寻找能把两单全部写进原容量的交错顺序。

    槽页空闲字节 = 页容量 - 页头 - 槽目录 - 活记录字节，只取决于两边各已
    执行的步数；压实在需要时自动发生且不改空闲。因此某一步能否落下只看
    当前网格点的空闲，与到达路径无关，网格搜索是精确的。
    """
    needs_a, slots_a, live_a = _profile(ops_a)
    needs_b, slots_b, live_b = _profile(ops_b)

    def free_at(i, j):
        return (parameter - HEADER_BYTES
                - SLOT_BYTES * (slots_a[i] + slots_b[j]) - live_a[i] - live_b[j])

    n, m = len(ops_a), len(ops_b)
    parents = {(0, 0): None}
    queue = deque([(0, 0)])
    goal = None
    while queue:
        i, j = queue.popleft()
        if (i, j) == (n, m):
            goal = (i, j)
            break
        free = free_at(i, j)
        if i < n and needs_a[i] <= free and (i + 1, j) not in parents:
            parents[(i + 1, j)] = (i, j)
            queue.append((i + 1, j))
        if j < m and needs_b[j] <= free and (i, j + 1) not in parents:
            parents[(i, j + 1)] = (i, j)
            queue.append((i, j + 1))
    if goal is None:
        return None, _stuck(ops_a, ops_b, parents, free_at, needs_a, needs_b)
    sides = []
    node = goal
    while parents[node] is not None:
        previous = parents[node]
        sides.append("a" if previous[0] < node[0] else "b")
        node = previous
    sides.reverse()
    return sides, None


def _stuck(ops_a, ops_b, reached, free_at, needs_a, needs_b):
    """走不通的可达死胡同里，被空间挡下的操作即卡住的履历。

    网格可达区域的尽头（两个方向的下一步都放不下的状态）就是顶牛的
    位置；在那里被挡下的操作，正是怎么交错都排不进去的履历。
    """
    n, m = len(ops_a), len(ops_b)
    blocked = {}
    for i, j in reached:
        moves = []
        if i < n:
            moves.append(("a", ops_a[i], needs_a[i]))
        if j < m:
            moves.append(("b", ops_b[j], needs_b[j]))
        if not moves:
            continue
        free = free_at(i, j)
        jammed = [(side, op, need) for side, op, need in moves if need > free]
        if len(jammed) < len(moves):
            continue
        for side, op, need in jammed:
            key = (side, op["step"])
            if key not in blocked or free > blocked[key][3]:
                blocked[key] = (side, op, need, free)
    return [
        {"来源": SIDE_LABELS[side], "原步骤": op["step"], "操作": ACTIONS[op["op"]],
         "履历": op["key"], "需要字节": need, "最多可腾": free}
        for side, op, need, free in blocked.values()
    ]


def _replay(sides, ops_a, ops_b, parameter):
    """把搜索到的交错顺序真正写进槽页，产出逐步证据。"""
    page = SlottedPage(parameter)
    ops = {"a": ops_a, "b": ops_b}
    index = {"a": 0, "b": 0}
    owner = {op["key"]: side for side in SIDES for op in ops[side]}
    rows = []
    compactions = 0
    peak_waste = 0
    for number, side in enumerate(sides, 1):
        op = ops[side][index[side]]
        index[side] += 1
        action = op["op"]
        key = op["key"]
        prior = page.find(key)
        pointer = page.slots[prior].offset if prior is not None else None
        slot_id = prior
        message = "完成"
        if action == "delete":
            slot_id = page.delete(key)
        else:
            write = page.insert if action == "insert" else page.update
            slot_id, compacted = write(key, op["note"].encode())
            if compacted:
                compactions += 1
                message = "压实后落盘"
        slot = page.slots[slot_id] if slot_id is not None else None
        peak_waste = max(peak_waste, page.fragmented)
        rows.append({
            "步骤": number, "来源": SIDE_LABELS[side], "原步骤": op["step"],
            "操作": ACTIONS[action], "履历": key, "槽号": slot_id,
            "原偏移": pointer, "现偏移": slot.offset if slot else None,
            "记录字节": slot.length if slot else None, "连续空闲": page.contiguous,
            "碎片字节": page.fragmented, "结果": message,
        })
    for slot_id, item in enumerate(page.slots):
        if item:
            rows.append({
                "步骤": None, "来源": SIDE_LABELS[owner[item.key]], "原步骤": None,
                "操作": "追溯地址", "履历": item.key, "槽号": slot_id,
                "原偏移": None, "现偏移": item.offset, "记录字节": item.length,
                "连续空闲": page.contiguous, "碎片字节": page.fragmented,
                "结果": page.read(slot_id).decode(),
            })
    return rows, compactions, peak_waste, page


def run_merge(payload_a, payload_b, parameter, choices):
    """合成一张交班单：能交错排进原容量就全写入，否则指出卡住的履历。"""
    if parameter < HEADER_BYTES:
        raise ValueError(f"至少需要 {HEADER_BYTES} 字节页头")
    choices = choices or {}
    ops_a = successful_ops(payload_a, parameter)
    ops_b = successful_ops(payload_b, parameter)
    conflicts = conflict_keys(ops_a, ops_b)
    extra = sorted(set(choices) - set(conflicts))
    if extra:
        raise ValueError(f"取舍包含未冲突的履历：{'、'.join(extra)}")
    missing = [key for key in conflicts if key not in choices]
    if missing:
        raise ValueError(f"以下履历尚未取舍：{'、'.join(missing)}")
    invalid = sorted(key for key, side in choices.items() if side not in SIDES)
    if invalid:
        raise ValueError(f"取舍只能是白班或夜班：{'、'.join(invalid)}")
    kept_a, kept_b = apply_choices(ops_a, ops_b, choices)
    conflict_rows = [
        {"履历": key, "取舍": SIDE_LABELS[choices[key]],
         "白班操作": sum(op["key"] == key for op in ops_a),
         "夜班操作": sum(op["key"] == key for op in ops_b)}
        for key in conflicts
    ]
    sides, stuck = _search(kept_a, kept_b, parameter)
    if sides is None:
        keys = sorted({entry["履历"] for entry in stuck})
        return {
            "fitted": False,
            "summary": f"原容量 {parameter} 字节怎么交错都装不下，"
                       f"{len(keys)} 条履历卡住：{'、'.join(keys)}。原凭据可照常查询。",
            "metrics": {"白班待排": len(kept_a), "夜班待排": len(kept_b),
                        "冲突履历": len(conflicts), "卡住履历": len(keys)},
            "conflicts": conflict_rows,
            "stuck": stuck,
            "rows": [],
        }
    rows, compactions, peak_waste, page = _replay(sides, kept_a, kept_b, parameter)
    live = sum(item is not None for item in page.slots)
    return {
        "fitted": True,
        "summary": f"交班合单写入 {len(sides)} 条操作（白班 {len(kept_a)}、夜班 {len(kept_b)}），"
                   f"保留 {live} 条履历，压实 {compactions} 次。",
        "metrics": {"活槽位": live, "拒绝操作": 0, "最大碎片": peak_waste,
                    "压实次数": compactions, "剩余字节": page.free,
                    "白班写入": len(kept_a), "夜班写入": len(kept_b),
                    "冲突履历": len(conflicts)},
        "conflicts": conflict_rows,
        "stuck": [],
        "rows": rows,
    }
