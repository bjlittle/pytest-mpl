import sys
import json
import shutil
import subprocess
from pathlib import Path

import matplotlib.ft2font
import pytest
from packaging.version import Version

from .helpers import assert_existence, diff_summary, patch_summary

# TODO: Remove this skip, see issue https://github.com/matplotlib/pytest-mpl/issues/159
pytest.skip(reason="temporarily disabled sub-tests", allow_module_level=True)

# Handle Matplotlib and FreeType versions
MPL_VERSION = Version(matplotlib.__version__)
FTV = matplotlib.ft2font.__freetype_version__.replace('.', '')
VERSION_ID = f"mpl{MPL_VERSION.major}{MPL_VERSION.minor}_ft{FTV}"
HASH_LIBRARY = Path(__file__).parent / 'hashes' / (VERSION_ID + ".json")
RESULT_LIBRARY = Path(__file__).parent / 'result_hashes' / (VERSION_ID + ".json")
HASH_LIBRARY_FLAG = rf'--mpl-hash-library={HASH_LIBRARY}'
FULL_BASELINE_PATH = Path(__file__).parent / 'baseline'

BASELINE_IMAGES_FLAG_REL = ['--mpl-baseline-path=baseline', '--mpl-baseline-relative']
BASELINE_IMAGES_FLAG_ABS = rf'--mpl-baseline-path={FULL_BASELINE_PATH}'

TEST_FILE = Path(__file__).parent / 'subtest.py'

# Global settings to update baselines when running pytest
# Note: when updating baseline make sure you don't commit "fixes"
# for tests that are expected to fail
# (See also `run_subtest` argument `update_baseline` and `update_summary`.)
UPDATE_BASELINE = False  # baseline images and hashes
UPDATE_SUMMARY = False  # baseline summaries


def run_subtest(baseline_summary_name, tmp_path, args, summaries=None, xfail=True,
                has_result_hashes=False, generating_hashes=False,
                update_baseline=UPDATE_BASELINE, update_summary=UPDATE_SUMMARY):
    """ Run pytest (within pytest) and check JSON summary report.

    Parameters
    ----------
    baseline_summary_name : str
        String of the filename without extension for the baseline summary.
    tmp_path : pathlib.Path
        Path of a temporary directory to store results.
    args : list
        Extra arguments to pass to pytest.
    summaries : tuple or list or set, optional, default=[]
        Summaries to generate in addition to `json`.
    xfail : bool, optional, default=True
        Whether the overall pytest run should fail.
    has_result_hashes : bool or str, optional, default=False
        Whether a hash library is expected to exist in the results directory.
        If a string, this is the name of the expected results file.
    generating_hashes : bool, optional, default=False
        Whether `--mpl-generate-hash-library` was specified and
        both of `--mpl-hash-library` and `hash_library=` were not.
    """
    # Parse arguments
    if summaries is None:
        summaries = []
    assert isinstance(summaries, (tuple, list, set))
    summaries = ','.join({'json'} | set(summaries))

    # Create the results path
    results_path = tmp_path / 'results'
    results_path.mkdir()

    # Configure the arguments to run the test
    pytest_args = [sys.executable, '-m', 'pytest', str(TEST_FILE)]
    mpl_args = ['--mpl', rf'--mpl-results-path={results_path.as_posix()}',
                f'--mpl-generate-summary={summaries}']
    if update_baseline:
        mpl_args += [rf'--mpl-generate-path={FULL_BASELINE_PATH}']
        if HASH_LIBRARY.exists():
            mpl_args += [rf'--mpl-generate-hash-library={HASH_LIBRARY}']

    # Run the test and record exit status
    status = subprocess.call(pytest_args + mpl_args + args)

    # If updating baseline, don't check summaries
    if update_baseline:
        assert status == 0
        if HASH_LIBRARY.exists():
            # Keep in sync. Use `git add -p` to commit specific lines.
            shutil.copy(HASH_LIBRARY, RESULT_LIBRARY)
        return

    # Ensure exit status is as expected
    if xfail:
        assert status != 0
    else:
        assert status == 0

    # Load summaries
    baseline_path = Path(__file__).parent / 'summaries'
    baseline_file = baseline_path / (baseline_summary_name + '.json')
    results_file = results_path / 'results.json'
    if update_summary:
        shutil.copy(results_file, baseline_file)
    with open(baseline_file, 'r') as f:
        baseline_summary = json.load(f)
    with open(results_file, 'r') as f:
        result_summary = json.load(f)

    # Apply version specific patches
    patch = baseline_path / (baseline_summary_name + f'_{VERSION_ID}.patch.json')
    if patch.exists():
        baseline_summary = patch_summary(baseline_summary, patch)
    # Note: version specific hashes should be handled by diff_summary instead

    # Compare summaries
    diff_summary(baseline_summary, result_summary,
                 baseline_hash_library=HASH_LIBRARY, result_hash_library=RESULT_LIBRARY,
                 generating_hashes=generating_hashes)

    # Ensure reported images exist
    assert_existence(result_summary, path=results_path)

    # Get expected name for the hash library saved to the results directory
    if isinstance(has_result_hashes, str):
        result_hash_file = tmp_path / 'results' / has_result_hashes
        has_result_hashes = True  # convert to bool after processing str
    else:
        result_hash_file = tmp_path / 'results' / HASH_LIBRARY.name

    # Compare the generated hash library to the expected hash library
    if has_result_hashes:
        assert result_hash_file.exists()
        with open(RESULT_LIBRARY, "r") as f:
            baseline = json.load(f)
        with open(result_hash_file, "r") as f:
            result = json.load(f)
        diff_summary({'a': baseline}, {'a': result})
    else:
        assert not result_hash_file.exists()


def test_default(tmp_path):
    run_subtest('test_default', tmp_path, [])


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_hash(tmp_path):
    run_subtest('test_hash', tmp_path, [HASH_LIBRARY_FLAG])


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_hybrid(tmp_path):
    run_subtest('test_hybrid', tmp_path, [HASH_LIBRARY_FLAG, BASELINE_IMAGES_FLAG_ABS])


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_results_always(tmp_path):
    run_subtest('test_results_always', tmp_path,
                [HASH_LIBRARY_FLAG, BASELINE_IMAGES_FLAG_ABS, '--mpl-results-always'],
                has_result_hashes=True)


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_html(tmp_path):
    run_subtest('test_results_always', tmp_path,
                [HASH_LIBRARY_FLAG, BASELINE_IMAGES_FLAG_ABS], summaries=['html'],
                has_result_hashes=True)
    assert (tmp_path / 'results' / 'fig_comparison.html').exists()
    assert (tmp_path / 'results' / 'extra.js').exists()
    assert (tmp_path / 'results' / 'styles.css').exists()


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_html_hashes_only(tmp_path):
    run_subtest('test_html_hashes_only', tmp_path, [HASH_LIBRARY_FLAG], summaries=['html'],
                has_result_hashes=True)
    assert (tmp_path / 'results' / 'fig_comparison.html').exists()
    assert (tmp_path / 'results' / 'extra.js').exists()
    assert (tmp_path / 'results' / 'styles.css').exists()


def test_html_images_only(tmp_path):
    run_subtest('test_html_images_only', tmp_path, [], summaries=['html'])
    assert (tmp_path / 'results' / 'fig_comparison.html').exists()
    assert (tmp_path / 'results' / 'extra.js').exists()
    assert (tmp_path / 'results' / 'styles.css').exists()


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_basic_html(tmp_path):
    run_subtest('test_results_always', tmp_path,
                [HASH_LIBRARY_FLAG, *BASELINE_IMAGES_FLAG_REL], summaries=['basic-html'],
                has_result_hashes=True)
    assert (tmp_path / 'results' / 'fig_comparison_basic.html').exists()


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_generate(tmp_path):
    # generating hashes and images; no testing
    run_subtest('test_generate', tmp_path,
                [rf'--mpl-generate-path={tmp_path}',
                 rf'--mpl-generate-hash-library={tmp_path / "test_hashes.json"}'],
                xfail=False, generating_hashes=True)


def test_generate_images_only(tmp_path):
    # generating images; no testing
    run_subtest('test_generate_images_only', tmp_path,
                [rf'--mpl-generate-path={tmp_path}'], xfail=False)


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_generate_hashes_only(tmp_path):
    # generating hashes; testing images
    run_subtest('test_generate_hashes_only', tmp_path,
                [rf'--mpl-generate-hash-library={tmp_path / "test_hashes.json"}'],
                generating_hashes=True)


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_html_generate(tmp_path):
    # generating hashes and images; no testing
    run_subtest('test_html_generate', tmp_path,
                [rf'--mpl-generate-path={tmp_path}',
                 rf'--mpl-generate-hash-library={tmp_path / "test_hashes.json"}'],
                summaries=['html'], xfail=False, has_result_hashes="test_hashes.json",
                generating_hashes=True)
    assert (tmp_path / 'results' / 'fig_comparison.html').exists()


def test_html_generate_images_only(tmp_path):
    # generating images; no testing
    run_subtest('test_html_generate_images_only', tmp_path,
                [rf'--mpl-generate-path={tmp_path}'],
                summaries=['html'], xfail=False)
    assert (tmp_path / 'results' / 'fig_comparison.html').exists()


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_html_generate_hashes_only(tmp_path):
    # generating hashes; testing images
    run_subtest('test_html_generate_hashes_only', tmp_path,
                [rf'--mpl-generate-hash-library={tmp_path / "test_hashes.json"}'],
                summaries=['html'], has_result_hashes="test_hashes.json", generating_hashes=True)
    assert (tmp_path / 'results' / 'fig_comparison.html').exists()


@pytest.mark.skipif(not HASH_LIBRARY.exists(), reason="No hash library for this mpl version")
def test_html_run_generate_hashes_only(tmp_path):
    # generating hashes; testing hashes
    run_subtest('test_html_hashes_only', tmp_path,
                [rf'--mpl-generate-hash-library={tmp_path / "test_hashes.json"}',
                 HASH_LIBRARY_FLAG],
                summaries=['html'], has_result_hashes="test_hashes.json")
    assert (tmp_path / 'results' / 'fig_comparison.html').exists()
