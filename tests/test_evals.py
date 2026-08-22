from evals.run_local import diagnosis_matches


def test_diagnosis_grader_accepts_semantically_equivalent_wording():
    assert diagnosis_matches(
        "database connection pool exhaustion",
        "Checkout exhausted its database connection pool, causing timeouts.",
    )


def test_diagnosis_grader_rejects_a_different_root_cause():
    assert not diagnosis_matches(
        "database connection pool exhaustion",
        "The upstream payment provider returned HTTP 503 responses.",
    )
