"""Independent registry: no default process, environment or arbitrary labels."""
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from .contracts import valid_fields

LONG = (.01, .05, .1, .25, .5, 1, 2, 5, 10, 30, 60, 120, 180)
SHORT = (.001, .005, .01, .05, .1, .5, 1, 5, 30, 120)
SPECS = {
    'http_requests': (Counter, ('route', 'method', 'status_class', 'outcome')),
    'http_request_duration_seconds': (Histogram, ('route', 'method')),
    'workflow_attempts': (Counter, ('kind', 'operation', 'outcome')),
    'workflow_node_duration_seconds': (Histogram, ('kind', 'node')),
    'model_transport': (Counter, ('purpose', 'status_class', 'outcome')),
    'model_transport_duration_seconds': (Histogram, ('purpose',)),
    'model_contract': (Counter, ('purpose', 'outcome')),
    'model_fallback': (Counter, ('purpose', 'reason')),
    'db_transaction_duration_seconds': (Histogram, ('database', 'outcome')),
    'db_write_begin_duration_seconds': (Histogram, ('database',)),
    'db_busy': (Counter, ('database', 'category')),
    'lock_wait_duration_seconds': (Histogram, ('role',)),
    'job_attempts': (Counter, ('job_kind', 'outcome')),
    'job_recoveries': (Counter, ('job_kind', 'outcome')),
    'job_queue_wait_seconds': (Histogram, ('job_kind',)),
    'job_execution_duration_seconds': (Histogram, ('job_kind',)),
    'observability_dropped': (Counter, ('sink', 'reason')),
    'observability_queue_depth': (Gauge, ()),
}


class Metrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.metrics = {}
        self.children = {}
        for name, (typ, labels) in SPECS.items():
            kwargs = {'buckets': SHORT if name.startswith(('db_', 'lock_')) else LONG} if typ is Histogram else {}
            self.metrics[name] = typ('health_' + name, name.replace('_', ' '), labels,
                                     registry=self.registry, **kwargs)

    def observe(self, name, value, labels, routes):
        import math
        if name not in SPECS or set(labels) != set(SPECS[name][1]) or not valid_fields(labels, routes):
            return False
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            return False
        typ, keys = SPECS[name]
        if keys:
            child_key = (name, *(labels[key] for key in keys))
            metric = self.children.get(child_key)
            if metric is None:
                metric = self.metrics[name].labels(**labels)
                self.children[child_key] = metric
        else:
            metric = self.metrics[name]
        if typ is Histogram:
            metric.observe(value)
        elif typ is Counter:
            metric.inc(value)
        else:
            metric.set(value)
        return True

    def render(self, quality=None):
        # A fresh registry prevents stale labels after deletion or window rollover.
        from prometheus_client.core import GaugeMetricFamily
        registry = CollectorRegistry()
        class Snapshot:
            def collect(inner):
                yield from self.registry.collect()
                if quality is None:
                    return
                for name, value_key in (('health_quality_numerator', 'numerator'),
                                        ('health_quality_denominator', 'denominator')):
                    metric = GaugeMetricFamily(name, 'Retained 30 day cohort contribution',
                        labels=['metric', 'measure', 'outcome', 'window'])
                    for row in quality:
                        if row['metric_key'] != 'plan_outcome':
                            metric.add_metric([row['metric_key'], row['measure'], row['outcome'], '30d'], row[value_key])
                    yield metric
                plans = GaugeMetricFamily('health_plan_cohort_count', 'Retained plan decisions',
                                          labels=['outcome', 'decision_source', 'window'])
                for row in quality:
                    if row['metric_key'] == 'plan_outcome':
                        plans.add_metric([row['outcome'], row['decision_source'], '30d'], row['numerator'])
                yield plans
        registry.register(Snapshot())
        return generate_latest(registry)
