from backend.jobs.tasks import process_pr_event

SAMPLE_DIFF_PAYLOAD = """diff --git a/README.md b/README.md
index 1234567..89abcde 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,3 @@
 # AutoDocs
 Automated PR Documentation
+Added new pipeline feature
"""


def test_process_pr_event_with_diff_text():
    payload = {
        "job_id": "test_job_123",
        "repository": "tarunmannava/autodocs",
        "pr_number": 42,
        "commit_sha": "abc1234",
        "diff_text": SAMPLE_DIFF_PAYLOAD,
    }

    result = process_pr_event.run(job_payload=payload)
    
    assert result["status"] == "success"
    assert result["job_id"] == "test_job_123"
    assert result["repository"] == "tarunmannava/autodocs"
    assert result["pr_number"] == 42
    assert result["files_changed_count"] == 1
