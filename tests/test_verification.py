from cst_material.domain import VerificationStatus
from cst_material.verification import Check, overall_status, p0_checklist


def test_default_checklist_is_not_run():
    checks = p0_checklist()
    assert len(checks) == 9
    assert overall_status(checks) == VerificationStatus.NOT_RUN


def test_failed_check_is_never_reported_as_pass():
    checks = [
        Check("one", VerificationStatus.PASS, ""),
        Check("two", VerificationStatus.FAIL, "failure"),
    ]
    assert overall_status(checks) == VerificationStatus.FAIL


def test_all_pass():
    assert overall_status([Check("one", VerificationStatus.PASS, "")]) == VerificationStatus.PASS
