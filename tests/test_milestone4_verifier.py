from scripts.verify_milestone4 import task_id


def test_verifier_redacts_task_arn_to_identifier():
    assert task_id("arn:aws:ecs:us-east-1:123456789012:task/cluster/abc123") == "abc123"
