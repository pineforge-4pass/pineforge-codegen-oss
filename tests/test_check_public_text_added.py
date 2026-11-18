"""Exercise the public-text check on committed objects in temporary repositories."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_public_text_added.py"
ADMISSION = ROOT / "tests/test_gke_pr_admission.py"


def load_source(path):
    spec = importlib.util.spec_from_file_location("public_text_test_input", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def suffix_token(suffix=None):
    return "Q7-" + (suffix if suffix is not None else "".join(("NE", "XT")))


def city():
    return "".join(("Tai", "pei"))


def digest_parts():
    return ["zef" + "ora", "quv" + "ali", "mep" + "uto"]


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "repo"
        self.root.mkdir()
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        self.environment.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        })
        self.git("init", "--quiet", "--object-format=sha1", "--initial-branch=main")
        self.write("README.txt", "Public documentation.\n")
        self.base = self.commit()
        self.digests = self.directory / "digests.txt"
        self.set_digests(["-".join(digest_parts())])

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.root), "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=" + str(self.directory / "no-hooks"), *args],
            env=self.environment, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def commit(self, message="Update public content"):
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "--no-gpg-sign", "--message", message)
        return self.git("rev-parse", "HEAD")

    def set_digests(self, terms):
        hashes = sorted({hashlib.sha256(term.encode("utf-8")).hexdigest() for term in terms})
        self.digests.write_text("# Test input.\n" + "\n".join(hashes) + "\n", encoding="utf-8")

    def invoke(self, *options, with_digests=True):
        digest_options = ["--digests", str(self.digests)] if with_digests else []
        return subprocess.run(
            [sys.executable, "-I", str(CHECKER), "--root", str(self.root),
             *digest_options, *options],
            env=self.environment, capture_output=True, text=True,
        )

    def scan(self, base=None, head="HEAD"):
        return self.invoke("--base", self.base if base is None else base, "--head", head)

    def failure(self, result, name):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL " + name, result.stdout)
        self.assertRegex(result.stdout, r"count=[1-9][0-9]*")
        self.assertNotIn("PASS coverage", result.stdout)

    def hit(self, result, location, class_id):
        self.failure(result, "findings")
        self.assertIn(location + " " + class_id, result.stdout.splitlines())

    def coverage(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("PASS coverage ")]
        self.assertEqual(len(lines), 1, result.stdout)
        fields = dict(part.split("=", 1) for part in lines[0].split()[2:])
        for name in ("files", "added_lines", "paths", "commits"):
            self.assertGreaterEqual(int(fields[name]), 0)
        self.assertNotIn("FAIL", result.stdout)
        return fields

    def added_sample(self, text):
        self.write("pineforge_codegen/sample.txt", text)
        self.commit()
        return self.scan()


class PublicTextTests(RepositoryCase):
    def test_e1_added_lines_fail_with_real_locations(self):
        self.coverage(self.invoke("--tree"))
        for suffix in ("".join(("C", "X")), "".join(("T", "OP")), "".join(("NE", "XT"))):
            with self.subTest(suffix_length=len(suffix)):
                token = suffix_token(suffix)
                result = self.added_sample("Ordinary first line.\n" + token + "\n")
                self.hit(result, "pineforge_codegen/sample.txt:2", "E1")
                self.assertNotIn(token, result.stdout)

    def test_e2_all_identifier_forms(self):
        prefixes = [("C", "R"), ("H", "O"), ("L", "B"), ("S", "M", "P"),
                    ("C", "G"), ("T", "X"), ("LI", "VE"), ("K",)]
        samples = ["".join(parts) + "-" + str(41) for parts in prefixes]
        samples += ["".join(("CG", "INT", str(41))), "S-" + str(1234)]
        for index, sample in enumerate(samples):
            with self.subTest(index=index):
                self.hit(self.added_sample(sample + "\n"), "pineforge_codegen/sample.txt:1", "E2")

    def test_e3_receipt_and_four_digit_forms(self):
        samples = [
            "".join(("r", "j", "-20", "123456", "q", "-sample")),
            "".join(("ti", "ck", "-", str(1234))),
        ]
        for index, sample in enumerate(samples):
            with self.subTest(index=index):
                self.hit(self.added_sample(sample + "\n"), "pineforge_codegen/sample.txt:1", "E3")

    def test_private_list_is_opt_in_and_other_classes_stay_enabled(self):
        term = "-".join(digest_parts())
        self.write("pineforge_codegen/sample.txt", term + "\n")
        self.commit()
        result = self.invoke("--base", self.base, with_digests=False)
        fields = self.coverage(result)
        self.assertEqual(fields["e4"], "not-run(no-private-list)")
        self.assertNotIn(" E4", result.stdout)
        listed = self.scan()
        self.hit(listed, "pineforge_codegen/sample.txt:1", "E4")
        self.assertNotIn(term, listed.stdout)
        self.assertNotIn(term, listed.stderr)
        self.write("pineforge_codegen/sample.txt", term + "\n" + suffix_token() + "\n")
        self.commit()
        result = self.invoke("--base", self.base, with_digests=False)
        self.hit(result, "pineforge_codegen/sample.txt:2", "E1")
        self.assertNotIn(" E4", result.stdout)

    def test_digest_windows_include_one_two_and_three_subtokens(self):
        parts = digest_parts()
        for size in range(1, len(parts) + 1):
            term = "-".join(parts[:size])
            self.set_digests([term])
            joined = "before_" + term + "_after"
            result = self.added_sample(joined + "\n")
            self.hit(result, "pineforge_codegen/sample.txt:1", "E4")
            for hidden in (term, joined):
                self.assertNotIn(hidden, result.stdout)
                self.assertNotIn(hidden, result.stderr)

    def test_digest_separator_case_and_identifier_normalization(self):
        parts = digest_parts()
        for separator in ("-", "_", "."):
            for transform in (
                str.lower, str.upper, str.title,
                lambda text: "".join(ch.upper() if i % 2 else ch for i, ch in enumerate(text)),
            ):
                spelling = transform(separator.join(parts))
                joined = "prefix_" + spelling + "_suffix"
                result = self.added_sample(joined + "\n")
                self.hit(result, "pineforge_codegen/sample.txt:1", "E4")
                for hidden in (spelling, joined, "-".join(parts)):
                    self.assertNotIn(hidden, result.stdout)
                    self.assertNotIn(hidden, result.stderr)

    def test_digest_paths_are_single_words_including_spaces(self):
        for index, separator in enumerate(("-", "_", ".", " ")):
            spelling = separator.join(digest_parts()).upper()
            if index % 2:
                spelling = "".join(ch.lower() if i % 2 else ch for i, ch in enumerate(spelling))
            path = "files/prefix_" + spelling + "_suffix.txt"
            self.write(path, "Public content.\n")
            self.commit()
            self.hit(self.scan(), path, "E4")

    def test_digest_subtokens_never_join_separate_words(self):
        parts = digest_parts()
        for text in (" ".join(parts), "-".join(parts[:2]) + " " + parts[2]):
            self.coverage(self.added_sample(text + "\n"))

    def test_e5_every_pattern_alternative(self):
        samples = [
            "".join(("Q", "-", "T", "OP")),
            "".join(("9Q", "-", "C", "X")),
            "".join(("C", "R", "-", str(123))),
            "".join(("L", "B", "-", str(4))),
            "".join(("r", "j", "-20", "123456", "z", "-sample")),
            "".join(("C", "X", "-alpha-beta")),
            "R" + str(7) + " " + "".join(("la", "ne")),
            "".join(("PER", "F-D", str(4))),
            "".join(("I", "NT", str(12))),
            "".join(("TRI", "AGE", str(3))),
            "".join(("AU", "DIT", str(3))),
            "".join(("super", "visor", " ", "call")),
            "".join(("ow", "ner", " ", "call")),
            "".join(("la", "ne", " ", "br", "ief")),
            "".join(("OW", "NER", " ", "DE", "CISION")),
            city(),
        ]
        for index, sample in enumerate(samples):
            with self.subTest(index=index):
                self.hit(self.added_sample(sample + "\n"), "pineforge_codegen/sample.txt:1", "E5")

    def test_ordinary_public_words_and_time_zone_are_clean(self):
        samples = [
            "on" + "-next", "On" + "-nExT", "script" + "-top", "Script" + "-ToP",
            "k" + "-" + str(1), "li" + "ve" + "-" + str(0), "Asia/" + city(),
            "".join(("ti", "ck", "-", str(12345))),
        ]
        fields = self.coverage(self.added_sample("\n".join(samples) + "\n"))
        self.assertEqual(int(fields["added_lines"]), len(samples))

    def test_city_after_time_is_not_the_time_zone_exception(self):
        self.hit(self.added_sample("09:30 " + city() + "\n"),
                 "pineforge_codegen/sample.txt:1", "E5")

    def test_e6_exact_path_segments_at_any_depth(self):
        segment = "." + "".join(("la", "ne"))
        for index, path in enumerate((segment + "/a.txt", "docs/" + segment + "/b.txt")):
            with self.subTest(index=index):
                self.write(path, "Public content.\n")
                self.commit()
                self.hit(self.scan(), path, "E6")

    def test_e6_in_added_text_and_longer_segment_controls(self):
        segment = "." + "".join(("la", "ne"))
        for text in ("docs/" + segment + "/data", "'" + segment + "'",
                     '"docs/' + segment + '"'):
            self.hit(self.added_sample(text + "\n"), "pineforge_codegen/sample.txt:1", "E6")
        for path in ("docs/" + segment + "-notes/a.txt", segment + ".txt"):
            self.write(path, "Public content.\n")
        self.write("pineforge_codegen/sample.txt",
                   "prefix+" + segment + "+suffix\n" + segment + "-notes\n")
        self.commit()
        self.coverage(self.scan())

    def test_added_path_is_checked_independently_of_content(self):
        path = "docs/" + suffix_token() + ".txt"
        self.write(path, "Public content.\n")
        self.commit()
        self.hit(self.scan(), path, "E1")

    def test_renamed_path_is_checked_without_readding_unchanged_text(self):
        token = suffix_token()
        self.write("original.txt", token + "\n")
        baseline = self.commit()
        path = "docs/" + suffix_token("".join(("C", "X"))) + ".txt"
        (self.root / "docs").mkdir()
        (self.root / "original.txt").rename(self.root / path)
        self.commit()
        result = self.scan(base=baseline)
        self.hit(result, path, "E1")
        self.assertNotIn(path + ":1 ", result.stdout)

    def test_every_commit_message_is_scanned_including_body(self):
        token = suffix_token()
        self.write("first.txt", "Public content.\n")
        first = self.commit("Public summary\n\n" + token)
        self.write("second.txt", "Further content.\n")
        self.commit("Another public summary")
        result = self.scan()
        self.hit(result, first[:8], "E1")
        self.assertNotIn(token, result.stdout)

    def test_clean_diff_reports_bound_nonzero_coverage(self):
        self.write("docs/example.txt", "Public content.\n")
        head = self.commit()
        fields = self.coverage(self.scan())
        self.assertEqual(fields["base"], self.base)
        self.assertEqual(fields["head"], head)
        self.assertEqual(fields["start"], self.git("merge-base", self.base, head))
        for name in ("files", "added_lines", "paths", "commits"):
            self.assertGreater(int(fields[name]), 0)

    def test_removal_only_does_not_scan_deleted_lines(self):
        self.write("existing.txt", "Retained.\n" + suffix_token() + "\n")
        baseline = self.commit()
        self.write("existing.txt", "Retained.\n")
        self.commit()
        fields = self.coverage(self.scan(base=baseline))
        self.assertEqual(int(fields["added_lines"]), 0)
        self.assertEqual(int(fields["paths"]), 0)
        self.assertGreater(int(fields["commits"]), 0)

    def test_modified_file_reports_only_added_line_numbers(self):
        retained = suffix_token("".join(("C", "X")))
        self.write("existing.txt", retained + "\nPublic content.\n")
        baseline = self.commit()
        self.write("existing.txt", retained + "\n" + suffix_token() + "\n")
        self.commit()
        result = self.scan(base=baseline)
        self.hit(result, "existing.txt:2", "E1")
        self.assertNotIn("existing.txt:1 ", result.stdout)

    def test_tree_scans_legacy_content_and_has_no_frozen_exemptions(self):
        self.assertEqual(load_source(CHECKER).FROZEN_EXEMPTIONS, {})
        self.write("existing.txt", suffix_token() + "\n")
        baseline = self.commit()
        self.write("new.txt", "Public content.\n")
        self.commit()
        self.coverage(self.scan(base=baseline))
        self.hit(self.invoke("--tree"), "existing.txt:1", "E1")

    def test_dirty_worktree_cannot_change_the_committed_scope(self):
        self.write("example.txt", suffix_token() + "\n")
        self.commit()
        self.write("example.txt", "Public replacement.\n")
        self.hit(self.scan(), "example.txt:1", "E1")
        self.commit()
        self.write("example.txt", suffix_token() + "\n")
        self.coverage(self.scan())

    def test_missing_unknown_and_zero_base_fail(self):
        self.write("example.txt", "Public content.\n")
        self.commit()
        for options, name in (
            ((), "base_required"),
            (("--base", "refs/heads/absent"), "git_base_unresolved"),
            (("--base", "0" * 40), "zero_base"),
        ):
            with self.subTest(name=name):
                self.failure(self.invoke(*options), name)

    def test_unknown_head_and_empty_commit_range_fail(self):
        head = self.commit()
        self.failure(self.scan(head="refs/heads/absent"), "git_head_unresolved")
        self.failure(self.scan(base=head, head=self.base), "empty_range")
        self.failure(self.scan(base=head, head=head), "empty_range")

    def test_shallow_repository_is_a_failure(self):
        self.commit()
        shallow = self.directory / "shallow"
        self.git("clone", "--quiet", "--depth=1", self.root.as_uri(), str(shallow))
        self.root = shallow
        self.assertEqual(self.git("rev-parse", "--is-shallow-repository"), "true")
        self.failure(self.scan(), "shallow_repository")

    def test_missing_empty_and_malformed_digest_inputs_fail(self):
        self.commit()
        valid = self.digests.read_bytes()
        self.digests = self.directory / "missing.txt"
        self.failure(self.scan(), "digest_read")
        for content, name in (
            (b"", "digest_empty"), (b"# Empty input.\n", "digest_empty"),
            (b"invalid\n", "digest_format"), (b"\xff\n", "digest_read"),
            (valid + valid.splitlines()[-1] + b"\n", "digest_order"),
            (b"f" * 64 + b"\n" + b"a" * 64 + b"\n", "digest_order"),
            (b"a" * 64 + b"\n# Late header.\n", "digest_format"),
        ):
            with self.subTest(name=name, length=len(content)):
                self.digests.write_bytes(content)
                self.failure(self.scan(), name)

    def test_malformed_digest_content_is_not_reported(self):
        self.commit()
        lines = self.digests.read_text(encoding="utf-8").splitlines()
        lines.append("".join(digest_parts()).title())
        self.digests.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = self.scan()
        self.failure(result, "digest_format")
        for line in lines:
            self.assertNotIn(line, result.stdout)
            self.assertNotIn(line, result.stderr)

    def test_undecodable_blob_is_not_clean(self):
        self.write("data.dat", b"\xff\xfe\n")
        self.commit()
        self.failure(self.scan(), "blob_decode")

    def test_nul_blobs_use_reversible_decoding_even_when_marked_binary(self):
        self.write(".gitattributes", "*.dat -diff\n")
        self.write("data.dat", b"\0\xff\nPublic content.\n")
        baseline = self.commit()
        self.write("data.dat", b"\0\xff\n" + suffix_token().encode("ascii") + b"\n")
        self.commit()
        self.hit(self.scan(base=baseline), "data.dat:2", "E1")

    def test_symlink_blob_is_scanned_without_following_its_target(self):
        (self.root / "example.txt").symlink_to(suffix_token())
        self.commit()
        self.hit(self.scan(), "example.txt:1", "E1")


class PublicTextInputTests(unittest.TestCase):
    def test_e5_external_pattern_provenance(self):
        pattern = load_source(CHECKER).ROLE_WORD_RE.pattern
        original = pattern.replace(r"\x20", " ").replace("Taipe[i]", city())
        self.assertEqual(len(original), 229)
        self.assertEqual(
            hashlib.sha256(original.encode("utf-8")).hexdigest(),
            "b9778723cf2978f4e4deba18fb125848d134cf937ce314d3431d07105187739b",
        )


class PublicTextHelperTests(RepositoryCase):
    def setUp(self):
        super().setUp()
        self.helper = load_source(ADMISSION).check_public_text_added
        self.event_path = self.directory / "event.json"

    def event(self, base=None, head=None):
        value = {
            "pull_request": {
                "base": {"sha": self.base if base is None else base},
                "head": {"sha": self.git("rev-parse", "HEAD") if head is None else head},
            },
        }
        self.event_path.write_text(json.dumps(value), encoding="utf-8")
        return {"GITHUB_EVENT_PATH": str(self.event_path)}

    def call(self, env):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return self.helper(self.root, env)

    def test_event_path_unset_is_a_named_failure(self):
        with self.assertRaisesRegex(ValueError, "FAIL event_path count=1"):
            self.call({})

    def test_missing_unreadable_or_invalid_event_file_fails(self):
        with self.assertRaisesRegex(ValueError, "FAIL event_read count=1"):
            self.call({"GITHUB_EVENT_PATH": str(self.event_path)})
        with self.assertRaisesRegex(ValueError, "FAIL event_read count=1"):
            self.call({"GITHUB_EVENT_PATH": str(self.directory)})
        for content in (b"{", b"\xff"):
            self.event_path.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "FAIL event_read count=1"):
                self.call({"GITHUB_EVENT_PATH": str(self.event_path)})

    def test_event_must_contain_a_pull_request(self):
        for value in ({}, [], {"pull_request": None}):
            self.event_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "FAIL event_pull_request count=1"):
                self.call({"GITHUB_EVENT_PATH": str(self.event_path)})

    def test_both_event_shas_must_be_lowercase_full_hashes(self):
        for name in ("base", "head"):
            for invalid in ("a" * 39, "A" * 40, "g" * 40, None):
                self.event()
                value = json.loads(self.event_path.read_text(encoding="utf-8"))
                value["pull_request"][name]["sha"] = invalid
                self.event_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "FAIL event_" + name + "_sha count=1"):
                    self.call({"GITHUB_EVENT_PATH": str(self.event_path)})

    def test_checkout_must_be_the_event_head(self):
        self.commit()
        with self.assertRaisesRegex(ValueError, "FAIL checkout_head count=1"):
            self.call(self.event(head=self.base))

    def test_clean_pr_range_passes_the_real_checker(self):
        self.write("docs/example.txt", "Public documentation.\n")
        head = self.commit()
        fields = self.coverage(self.call(self.event()))
        self.assertEqual(fields["base"], self.base)
        self.assertEqual(fields["head"], head)
        self.assertGreater(int(fields["added_lines"]), 0)

    def test_helper_propagates_checker_findings(self):
        self.write("pineforge_codegen/sample.txt", suffix_token() + "\n")
        self.commit()
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.call(self.event())
        self.assertEqual(raised.exception.returncode, 1)
        self.assertIn("pineforge_codegen/sample.txt:1 E1", raised.exception.stdout)
        self.assertIn("FAIL findings", raised.exception.stdout)

    def test_pr_behind_main_does_not_restore_main_deletions_in_scope(self):
        token = suffix_token()
        self.write("existing.txt", token + "\n")
        common = self.commit()
        self.git("checkout", "--quiet", "-b", "topic")
        self.write("docs/example.txt", "Public documentation.\n")
        head = self.commit()
        self.git("checkout", "--quiet", "main")
        self.write("existing.txt", "")
        base = self.commit()
        self.git("checkout", "--quiet", "topic")
        self.assertEqual(self.git("merge-base", base, head), common)
        self.assertIn("+" + token, self.git("diff", base, head, "--", "existing.txt"))
        self.assertEqual(self.git("diff", common, head, "--", "existing.txt"), "")
        fields = self.coverage(self.call(self.event(base=base, head=head)))
        self.assertEqual(fields["start"], common)
        self.assertEqual(fields["base"], base)
        self.assertEqual(fields["head"], head)
