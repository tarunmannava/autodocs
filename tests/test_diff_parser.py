from repo_manager.diff import is_ignored_file, parse_unified_diff

SAMPLE_DIFF = """diff --git a/backend/main.py b/backend/main.py
index 1234567..89abcde 100644
--- a/backend/main.py
+++ b/backend/main.py
@@ -1,3 +1,4 @@
 def create_app():
     app = FastAPI()
+# Add new router
     return app
diff --git a/package-lock.json b/package-lock.json
index 1234567..89abcde 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,3 @@
 {
-  "name": "old"
+  "name": "new"
 }
"""


def test_is_ignored_file():
    assert is_ignored_file("package-lock.json") is True
    assert is_ignored_file("poetry.lock") is True
    assert is_ignored_file("assets/image.png") is True
    assert is_ignored_file("backend/main.py") is False
    assert is_ignored_file("README.md") is False


def test_parse_unified_diff_with_filtering():
    changed_files = parse_unified_diff(SAMPLE_DIFF, filter_ignored=True)
    assert len(changed_files) == 1
    
    file_info = changed_files[0]
    assert file_info.path == "backend/main.py"
    assert file_info.added_lines == 1
    assert file_info.removed_lines == 0
    assert len(file_info.hunks) == 1
    assert "# Add new router" in file_info.hunks[0].patch_text


def test_parse_unified_diff_without_filtering():
    changed_files = parse_unified_diff(SAMPLE_DIFF, filter_ignored=False)
    paths = [f.path for f in changed_files]
    assert "backend/main.py" in paths
    assert "package-lock.json" in paths
    assert len(changed_files) == 2


def test_empty_diff():
    assert parse_unified_diff("") == []
