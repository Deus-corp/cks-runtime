from cks_runtime.diagnostics.aggregator import (
    DiagnosticAggregator,
)
from cks_runtime.diagnostics.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticSource,
)


def create_runtime_info():

    return Diagnostic(
        message="Session created",
        source=DiagnosticSource.RUNTIME,
        severity=DiagnosticSeverity.INFO,
    )


def create_runtime_error():

    return Diagnostic(
        message="Failure",
        source=DiagnosticSource.RUNTIME,
        severity=DiagnosticSeverity.ERROR,
    )


def test_empty_aggregator():

    agg = DiagnosticAggregator()

    assert agg.empty

    assert agg.count() == 0

    assert len(agg) == 0

    assert agg.all() == ()


def test_add_runtime_diagnostic():

    agg = DiagnosticAggregator()

    diagnostic = create_runtime_info()

    agg.add(diagnostic)

    assert not agg.empty

    assert agg.count() == 1

    assert len(agg) == 1

    assert agg.all()[0] == diagnostic


def test_extend():

    agg = DiagnosticAggregator()

    diagnostics = (
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
    )

    agg.extend(diagnostics)

    assert agg.count() == 2


def test_clear():

    agg = DiagnosticAggregator()

    agg.add(
        create_runtime_info()
    )

    agg.clear()

    assert agg.empty

    assert agg.count() == 0

    assert agg.all() == ()


def test_has_errors():

    agg = DiagnosticAggregator()

    agg.add(
        create_runtime_error()
    )

    assert agg.has_errors()


def test_has_errors_false():

    agg = DiagnosticAggregator()

    agg.add(
        create_runtime_info()
    )

    assert not agg.has_errors()


def test_diagnostics_are_returned_as_tuple():

    agg = DiagnosticAggregator()

    agg.add(
        create_runtime_info()
    )

    diagnostics = agg.all()

    assert isinstance(
        diagnostics,
        tuple,
    )

    assert len(diagnostics) == 1


def test_iterator():

    agg = DiagnosticAggregator()

    diagnostic = create_runtime_info()

    agg.add(diagnostic)

    assert list(agg) == [diagnostic]


def test_string_representation():

    diagnostic = Diagnostic(
        message="Failure",
        source=DiagnosticSource.RUNTIME,
        severity=DiagnosticSeverity.ERROR,
    )

    text = str(diagnostic)

    assert "Failure" in text

    assert "runtime" in text

    assert "ERROR" in text
