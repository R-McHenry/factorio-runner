"""fkcore.contract: every sealed instance's generated contract (PLAN-MODULES phase 4).

One line per sealed module, one part per port:

    rib0: in iron-plate 4.0/s via bus tap n7 (got 4.0) | out iron-gear-wheel 2.0/s via box0/c0

``in`` ports show the rate the module needs at full speed (its consumers' appetite) and what it
actually got in the rate model; ``out`` ports show the rate the module makes at full speed and where
it goes. The numbers are the model's, so a starved rib reads ``(got 0.0)`` against its contract --
the at-a-glance proof that every rib is fed through the bus. Generated, never written.
"""
from __future__ import annotations

from . import rates


def _where(flat, key):
    """How the report names one end of a net: a bus's port with its tile, a port by name, else the
    machine key."""
    m = flat.machines.get(key)
    if m is not None:
        kind, at = m.extra.get("port"), m.extra.get("at")
        if kind in ("load", "tap", "mouth"):
            # 2.0.46: the bus by its own name ("bus tap n7" for a bus called bus, "febus mouth
            # in1" where a base has several)
            return f"{m.inst} {'mouth' if m.extra.get('mouth') else kind} {at}"
    port = flat.ports.get(key)
    if port is not None:
        return f"port {port.name}"
    return key


def _net_rate(model, nets, item, field):
    vals = [getattr(model.nets[(n, item)], field) for n in nets if (n, item) in model.nets]
    return max(vals, default=0.0)


def contract(flat, model=None):
    """The contract lines of every sealed instance, in path order."""
    model = model or rates.solve(flat)
    out = []
    for path in sorted(flat.insts):
        inst = flat.insts[path]
        if inst.module is None:
            continue
        keys = sorted((k for k in flat.ports
                       if k.startswith(path + "/") and "/" not in k[len(path) + 1:]),
                      key=lambda k: flat.ports[k].name)
        if not keys:
            continue
        parts = []
        for key in keys:
            port = flat.ports[key]
            item = port.items[0] if port.items else None
            if port.dir == "out":
                inside = flat._in_hold.get((key, item), [])
                outside = flat._out_hold.get((key, item), [])
            else:
                inside = flat._out_hold.get((key, item), [])
                outside = flat._in_hold.get((key, item), [])
            both = list(dict.fromkeys(inside + outside))
            partners = []
            for net in outside:
                if net in flat.nets:
                    partners += [k for k, _i in flat.nets[net].drivers + flat.nets[net].consumers]
            partners = [k for k in dict.fromkeys(partners) if k != key and not k.startswith("~")]
            via = ", ".join(_where(flat, k) for k in partners) or "nothing"
            got = _net_rate(model, both, item, "delivered")
            if port.dir == "out":
                # 2.0.46: what the module's own makers put through the port, at full speed and as
                # solved; a net a bus pool absorbed (an out port into a mouth or a load) has no
                # delivered or can_make of its own, so the nets' rows read 0.0 there
                makers = [k for n in inside if n in flat.nets for k, _i in flat.nets[n].drivers
                          if k != key and k in flat.machines and not flat.machines[k].extra.get("port")]
                if makers:
                    want = sum(flat.machines[k].makes.get(item, 0.0) for k in makers)
                    got = sum(model.rate(k, item) for k in makers)
                else:
                    want = _net_rate(model, both, item, "can_make")
                parts.append(f"out {item} {want:.1f}/s via {via} (got {got:.1f})")
            else:
                want = _net_rate(model, both, item, "would_take")
                parts.append(f"{port.dir} {item} {want:.1f}/s via {via} (got {got:.1f})")
        out.append(f"{path}: " + " | ".join(parts))
    return out
