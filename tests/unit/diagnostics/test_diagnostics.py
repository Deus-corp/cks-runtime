from cks_runtime.diagnostics.aggregator import DiagnosticAggregator
from cks_runtime.diagnostics.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticSource,
)


def test_empty_aggregator():
    agg = DiagnosticAggregator()

    assert agg.count() == 0
    assert agg.all() == ()


def test_add_runtime_diagnostic():
    agg = DiagnosticAggregator()

    d = Diagnostic(
        message="Session created",
        source=DiagnosticSource.RUNTIME,
        severity=DiagnosticSeverity.INFO,
    )

    agg.add(d)

    assert agg.count() == 1
    assert agg.all()[0] == d


def test_extend():
    agg = DiagnosticAggregator()

    ds = [
        Diagnostic(
            message="A",
            source=DiagnosticSource.RUNTIME,
            severity=DiagnosticSeverity.INFO,
        ),
        Diagnostic(
            message="B",
            source=DiagnosticSource.CORE,
            severity=DiagnosticSeverity.WARNING,
        ),
    ]

    agg.extend(ds)

    assert agg.count() == 2


def test_clear():
    agg = DiagnosticAggregator()

    agg.add(
        Diagnostic(
            message="Test",
            source=DiagnosticSource.RUNTIME,
            severity=DiagnosticSeverity.INFO,
        )
    )

    agg.clear()

    assert agg.count() == 0


def test_has_errors():
    agg = DiagnosticAggregator()

    agg.add(
        Diagnostic(
            message="Failure",
            source=DiagnosticSource.RUNTIME,
            severity=DiagnosticSeverity.ERROR,
        )
    )

    assert agg.has_errors()

def test_diagnostics_are_returned_as_tuple():

    agg = DiagnosticAggregator()

    agg.add(
        Diagnostic(
            message="Test",
            source=DiagnosticSource.RUNTIME,
            severity=DiagnosticSeverity.INFO,
        )
    )

    assert isinstance(
        agg.all(),
        tuple,
    )