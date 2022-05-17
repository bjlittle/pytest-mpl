# Copyright (c) 2015, Thomas P. Robitaille
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# The code below includes code adapted from WCSAxes, which is released
# under a 3-clause BSD license and can be found here:
#
#   https://github.com/astrofrog/wcsaxes

import io
import os
import json
import shutil
import inspect
import logging
import tempfile
import warnings
import contextlib
from pathlib import Path
from functools import wraps
from urllib.request import urlopen

import pytest

from .kernels import KERNEL_SHA256, kernel_factory
from .summary.html import generate_summary_basic_html, generate_summary_html

SUPPORTED_FORMATS = {'html', 'json', 'basic-html'}

SHAPE_MISMATCH_ERROR = """Error: Image dimensions did not match.
  Expected shape: {expected_shape}
    {expected_path}
  Actual shape: {actual_shape}
    {actual_path}"""

#: The default matplotlib backend.
DEFAULT_BACKEND = "agg"

#: The default hamming distance bit tolerance for "similar" imagehash hashes.
DEFAULT_HAMMING_TOLERANCE = 2

#: The default imagehash hash size (N), resulting in a hash of N**2 bits.
DEFAULT_HASH_SIZE = 16

#: The default algorithm to use for image hashing.
DEFAULT_KERNEL = KERNEL_SHA256

#: The default pytest-mpl marker.
DEFAULT_MARKER = "mpl_image_compare"

#: The default option to remove text from matplotlib plot.
DEFAULT_REMOVE_TEXT = False

#: The default image RMS tolerance.
DEFAULT_RMS_TOLERANCE = 2

#: The default matplotlib plot style.
DEFAULT_STYLE = "classic"


def pathify(path):
    """
    Remove non-path safe characters.
    """
    path = Path(path)
    ext = ''
    if path.suffixes[-1] == '.png':
        ext = '.png'
        path = str(path).split(ext)[0]
    path = str(path)
    path = path.replace('[', '_').replace(']', '_')
    path = path.replace('/', '_')
    if path.endswith('_'):
        path = path[:-1]
    return Path(path + ext)


def pytest_report_header(config, startdir):
    import matplotlib
    import matplotlib.ft2font
    return ["Matplotlib: {0}".format(matplotlib.__version__),
            "Freetype: {0}".format(matplotlib.ft2font.__freetype_version__)]


def pytest_addoption(parser):
    group = parser.getgroup("matplotlib image comparison")

    msg = 'Enable comparison of matplotlib figures to reference files.'
    group.addoption('--mpl', help=msg, action='store_true')

    msg = ('Directory to generate reference images in, relative to '
           'location where py.test is run.')
    group.addoption('--mpl-generate-path', help=msg, action='store')

    msg = ('Filepath to save a generated hash library, relative to '
           'location where py.test is run.')
    group.addoption('--mpl-generate-hash-library', help=msg, action='store')

    msg = ('Directory containing baseline images, relative to location '
           'where py.test is run unless --mpl-baseline-relative is given. '
           'This can also be a URL or a set of comma-separated URLs '
           '(in case mirrors are specified).')
    group.addoption('--mpl-baseline-path', help=msg, action='store')

    msg = 'Interpret the baseline directory as relative to the test location.'
    group.addoption("--mpl-baseline-relative", help=msg, action="store_true")

    msg = ('JSON library of image hashes, relative to location where '
           'py.test is run.')
    group.addoption('--mpl-hash-library', help=msg, action='store')

    msg = ('Generate a summary report of any failed tests, in '
           '--mpl-results-path. The type of the report should be specified. '
           'Supported types are `html`, `json` and `basic-html`. '
           'Multiple types can be specified separated by commas.')
    group.addoption('--mpl-generate-summary', help=msg, action='store')

    msg = ('Directory for test results, relative to location where py.test '
           'is run.')
    group.addoption('--mpl-results-path', help=msg, action='store')
    parser.addini('mpl-results-path', help=msg)

    msg = ('Always compare to baseline images and save result images, even '
           'for passing tests. This option is automatically applied when '
           'generating a HTML summary.')
    group.addoption('--mpl-results-always', help=msg, action='store_true')
    parser.addini('mpl-results-always', help=msg)

    msg = 'Use fully qualified test name as the filename.'
    parser.addini('mpl-use-full-test-name', help=msg, type='bool')

    msg = ('Algorithm to be used for hashing images. Supported kernels are '
           '`sha256` (default) and `phash`.')
    group.addoption('--mpl-kernel', help=msg, action='store')
    parser.addini('mpl-kernel', help=msg)

    msg = 'The hash size (N) used to generate a N**2 bit image hash.'
    group.addoption('--mpl-hash-size', help=msg, action='store')
    parser.addini('mpl-hash-size', help=msg)

    msg = 'Hamming distance bit tolerance for similar image hashes.'
    group.addoption('--mpl-hamming-tolerance', help=msg, action='store')
    parser.addini('mpl-hamming-tolerance', help=msg)


def pytest_configure(config):

    config.addinivalue_line('markers',
                            f"{DEFAULT_MARKER}: Compares matplotlib figures "
                            "against a baseline image")

    if (config.getoption("--mpl") or
            config.getoption("--mpl-generate-path") is not None or
            config.getoption("--mpl-generate-hash-library") is not None):

        baseline_dir = config.getoption("--mpl-baseline-path")
        generate_dir = config.getoption("--mpl-generate-path")
        generate_hash_lib = config.getoption("--mpl-generate-hash-library")
        option = "mpl-results-path"
        results_dir = config.getoption(f"--{option}") or config.getini(option)
        hash_library = config.getoption("--mpl-hash-library")
        generate_summary = config.getoption("--mpl-generate-summary")
        option = "mpl-results-always"
        results_always = config.getoption(f"--{option}") or config.getini(option)

        if config.getoption("--mpl-baseline-relative"):
            baseline_relative_dir = config.getoption("--mpl-baseline-path")
        else:
            baseline_relative_dir = None

        # Note that results_dir is an empty string if not specified
        if not results_dir:
            results_dir = None

        if generate_dir is not None:
            if baseline_dir is not None:
                wmsg = ("Ignoring --mpl-baseline-path since "
                        "--mpl-generate-path is set")
                warnings.warn(wmsg)

        protocols = ("https", "http")
        if baseline_dir is not None and not baseline_dir.startswith(protocols):
            baseline_dir = os.path.abspath(baseline_dir)
        if generate_dir is not None:
            baseline_dir = os.path.abspath(generate_dir)
        if results_dir is not None:
            results_dir = os.path.abspath(results_dir)

        plugin = ImageComparison(config,
                                 baseline_dir=baseline_dir,
                                 baseline_relative_dir=baseline_relative_dir,
                                 generate_dir=generate_dir,
                                 results_dir=results_dir,
                                 hash_library=hash_library,
                                 generate_hash_library=generate_hash_lib,
                                 generate_summary=generate_summary,
                                 results_always=results_always)
        config.pluginmanager.register(plugin)

    else:

        config.pluginmanager.register(FigureCloser(config))


@contextlib.contextmanager
def switch_backend(backend):
    import matplotlib
    import matplotlib.pyplot as plt
    prev_backend = matplotlib.get_backend().lower()
    if prev_backend != backend.lower():
        plt.switch_backend(backend)
        yield
        plt.switch_backend(prev_backend)
    else:
        yield


def close_mpl_figure(fig):
    "Close a given matplotlib Figure. Any other type of figure is ignored"

    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    # We only need to close actual Matplotlib figure objects. If
    # we are dealing with a figure-like object that provides
    # savefig but is not a real Matplotlib object, we shouldn't
    # try closing it here.
    if isinstance(fig, Figure):
        plt.close(fig)


def get_marker(item, marker_name):
    if hasattr(item, 'get_closest_marker'):
        return item.get_closest_marker(marker_name)
    else:
        # "item.keywords.get" was deprecated in pytest 3.6
        # See https://docs.pytest.org/en/latest/mark.html#updating-code
        return item.keywords.get(marker_name)


def path_is_not_none(apath):
    return Path(apath) if apath is not None else apath


class ImageComparison:

    def __init__(self,
                 config,
                 baseline_dir=None,
                 baseline_relative_dir=None,
                 generate_dir=None,
                 results_dir=None,
                 hash_library=None,
                 generate_hash_library=None,
                 generate_summary=None,
                 results_always=False,
                 ):
        self.config = config
        self.baseline_dir = baseline_dir
        self.baseline_relative_dir = path_is_not_none(baseline_relative_dir)
        self.generate_dir = path_is_not_none(generate_dir)
        self.results_dir = path_is_not_none(results_dir)
        self.hash_library = path_is_not_none(hash_library)
        self.generate_hash_library = path_is_not_none(generate_hash_library)
        if generate_summary:
            generate_summary = {i.lower() for i in generate_summary.split(',')}
            unsupported_formats = generate_summary - SUPPORTED_FORMATS
            if len(unsupported_formats) > 0:
                raise ValueError(f"The mpl summary type(s) '{sorted(unsupported_formats)}' "
                                 "are not supported.")
            # When generating HTML always apply `results_always`
            if generate_summary & {'html', 'basic-html'}:
                results_always = True
        self.generate_summary = generate_summary
        self.results_always = results_always

        # Configure hashing kernel options.
        option = "mpl-hash-size"
        hash_size = int(config.getoption(f"--{option}") or
                        config.getini(option) or DEFAULT_HASH_SIZE)
        self.hash_size = hash_size

        option = "mpl-hamming-tolerance"
        hamming_tolerance = int(config.getoption(f"--{option}") or
                                config.getini(option) or
                                DEFAULT_HAMMING_TOLERANCE)
        self.hamming_tolerance = hamming_tolerance

        # Configure the hashing kernel.
        option = "mpl-kernel"
        kernel = config.getoption(f"--{option}") or config.getini(option)
        if kernel:
            requested = str(kernel).lower()
            if requested not in kernel_factory:
                emsg = f"Unrecognised hashing kernel {kernel!r} not supported."
                raise ValueError(emsg)
            kernel = requested
        else:
            kernel = DEFAULT_KERNEL
        print(f"{kernel_factory=}")
        self.kernel = kernel_factory[kernel](self)

        # Generate the containing dir for all test results
        if not self.results_dir:
            self.results_dir = Path(tempfile.mkdtemp(dir=self.results_dir))
        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Decide what to call the downloadable results hash library
        if self.hash_library is not None:
            self.results_hash_library_name = self.hash_library.name
        else:  # Use the first filename encountered in a `hash_library=` kwarg
            self.results_hash_library_name = None

        # We need global state to store all the hashes generated over the run
        self._generated_hash_library = {}
        self._test_results = {}
        self._test_stats = None

        # https://stackoverflow.com/questions/51737378/how-should-i-log-in-my-pytest-plugin
        # turn debug prints on only if "-vv" or more passed
        level = logging.DEBUG if config.option.verbose > 1 else logging.INFO
        logging.basicConfig(level=level)
        self.logger = logging.getLogger('pytest-mpl')

    def get_compare(self, item):
        """
        Return the mpl_image_compare marker for the given item.
        """
        return get_marker(item, DEFAULT_MARKER)

    def generate_filename(self, item):
        """
        Given a pytest item, generate the figure filename.
        """
        if self.config.getini('mpl-use-full-test-name'):
            filename = self.generate_test_name(item) + '.png'
        else:
            compare = self.get_compare(item)
            # Find test name to use as plot name
            filename = compare.kwargs.get('filename', None)
            if filename is None:
                filename = item.name + '.png'

        filename = str(pathify(filename))
        return filename

    def generate_test_name(self, item):
        """
        Generate a unique name for the hash for this test.
        """
        return f"{item.module.__name__}.{item.name}"

    def make_test_results_dir(self, item):
        """
        Generate the directory to put the results in.
        """
        test_name = pathify(self.generate_test_name(item))
        results_dir = self.results_dir / test_name
        results_dir.mkdir(exist_ok=True, parents=True)
        return results_dir

    def baseline_directory_specified(self, item):
        """
        Returns `True` if a non-default baseline directory is specified.
        """
        compare = self.get_compare(item)
        item_baseline_dir = compare.kwargs.get('baseline_dir', None)
        return item_baseline_dir or self.baseline_dir or self.baseline_relative_dir

    def get_baseline_directory(self, item):
        """
        Return a full path to the baseline directory, either local or remote.

        Using the global and per-test configuration return the absolute
        baseline dir, if the baseline file is local else return base URL.
        """
        compare = self.get_compare(item)
        baseline_dir = compare.kwargs.get('baseline_dir', None)
        if baseline_dir is None:
            if self.baseline_dir is None:
                baseline_dir = Path(item.fspath).parent / 'baseline'
            else:
                if self.baseline_relative_dir:
                    # baseline dir is relative to the current test
                    baseline_dir = Path(item.fspath).parent / self.baseline_relative_dir
                else:
                    # baseline dir is relative to where pytest was run
                    baseline_dir = self.baseline_dir

        baseline_remote = (isinstance(baseline_dir, str) and  # noqa
                           baseline_dir.startswith(('http://', 'https://')))
        if not baseline_remote:
            return Path(item.fspath).parent / baseline_dir

        return baseline_dir

    def _download_file(self, baseline, filename):
        # Note that baseline can be a comma-separated list of URLs that we can
        # then treat as mirrors
        for base_url in baseline.split(','):
            try:
                u = urlopen(base_url + filename)
                content = u.read()
            except Exception as e:
                self.logger.info(f'Downloading {base_url + filename} failed: {repr(e)}')
            else:
                break
        else:
            raise Exception("Could not download baseline image from any of the "
                            "available URLs")
        result_dir = Path(tempfile.mkdtemp())
        filename = result_dir / 'downloaded'
        with open(str(filename), 'wb') as tmpfile:
            tmpfile.write(content)
        return Path(filename)

    def obtain_baseline_image(self, item, target_dir):
        """
        Copy the baseline image to our working directory.

        If the image is remote it is downloaded, if it is local it is copied to
        ensure it is kept in the event of a test failure.
        """
        filename = self.generate_filename(item)
        baseline_dir = self.get_baseline_directory(item)
        baseline_remote = (isinstance(baseline_dir, str) and  # noqa
                           baseline_dir.startswith(('http://', 'https://')))
        if baseline_remote:
            # baseline_dir can be a list of URLs when remote, so we have to
            # pass base and filename to download
            baseline_image = self._download_file(baseline_dir, filename)
        else:
            baseline_image = (baseline_dir / filename).absolute()

        return baseline_image

    def generate_baseline_image(self, item, fig):
        """
        Generate reference figures.
        """
        compare = self.get_compare(item)
        savefig_kwargs = compare.kwargs.get('savefig_kwargs', {})

        if not os.path.exists(self.generate_dir):
            os.makedirs(self.generate_dir)

        baseline_filename = self.generate_filename(item)
        baseline_path = (self.generate_dir / baseline_filename).absolute()
        fig.savefig(str(baseline_path), **savefig_kwargs)

        close_mpl_figure(fig)

        return baseline_path

    def generate_image_hash(self, item, fig):
        """
        For a `matplotlib.figure.Figure`, returns the hash generated by the
        kernel as a hexadecimal string.
        """
        compare = self.get_compare(item)
        savefig_kwargs = compare.kwargs.get('savefig_kwargs', {})

        imgdata = io.BytesIO()

        fig.savefig(imgdata, **savefig_kwargs)

        hash = self.kernel.generate_hash(imgdata)
        imgdata.close()

        close_mpl_figure(fig)
        return hash

    def compare_image_to_baseline(self, item, fig, result_dir, summary=None):
        """
        Compare a test image to a baseline image.
        """
        from matplotlib.image import imread
        from matplotlib.testing.compare import compare_images

        if summary is None:
            summary = {}

        compare = self.get_compare(item)
        tolerance = compare.kwargs.get('tolerance', DEFAULT_RMS_TOLERANCE)
        savefig_kwargs = compare.kwargs.get('savefig_kwargs', {})

        baseline_image_ref = self.obtain_baseline_image(item, result_dir)

        test_image = (result_dir / "result.png").absolute()
        fig.savefig(str(test_image), **savefig_kwargs)
        summary['result_image'] = test_image.relative_to(self.results_dir).as_posix()

        if not os.path.exists(baseline_image_ref):
            summary['status'] = 'failed'
            summary['image_status'] = 'missing'
            error_message = ("Image file not found for comparison test in: \n\t"
                             f"{self.get_baseline_directory(item)}\n"
                             "(This is expected for new tests.)\n"
                             "Generated Image: \n\t"
                             f"{test_image}")
            summary['status_msg'] = error_message
            return error_message

        # setuptools may put the baseline images in non-accessible places,
        # copy to our tmpdir to be sure to keep them in case of failure
        baseline_image = (result_dir / "baseline.png").absolute()
        shutil.copyfile(baseline_image_ref, baseline_image)
        summary['baseline_image'] = baseline_image.relative_to(self.results_dir).as_posix()

        # Compare image size ourselves since the Matplotlib
        # exception is a bit cryptic in this case and doesn't show
        # the filenames
        expected_shape = imread(str(baseline_image)).shape[:2]
        actual_shape = imread(str(test_image)).shape[:2]
        if expected_shape != actual_shape:
            summary['status'] = 'failed'
            summary['image_status'] = 'diff'
            error_message = SHAPE_MISMATCH_ERROR.format(expected_path=baseline_image,
                                                        expected_shape=expected_shape,
                                                        actual_path=test_image,
                                                        actual_shape=actual_shape)
            summary['status_msg'] = error_message
            return error_message

        results = compare_images(str(baseline_image), str(test_image), tol=tolerance, in_decorator=True)
        summary['tolerance'] = tolerance
        if results is None:
            summary['status'] = 'passed'
            summary['image_status'] = 'match'
            summary['status_msg'] = 'Image comparison passed.'
            return None
        else:
            summary['status'] = 'failed'
            summary['image_status'] = 'diff'
            summary['rms'] = results['rms']
            diff_image = (result_dir / 'result-failed-diff.png').absolute()
            summary['diff_image'] = diff_image.relative_to(self.results_dir).as_posix()
            template = ['Error: Image files did not match.',
                        'RMS Value: {rms}',
                        'Expected:  \n    {expected}',
                        'Actual:    \n    {actual}',
                        'Difference:\n    {diff}',
                        'Tolerance: \n    {tol}', ]
            error_message = '\n  '.join([line.format(**results) for line in template])
            summary['status_msg'] = error_message
            return error_message

    def load_hash_library(self, library_path):
        with open(str(library_path)) as fp:
            return json.load(fp)

    def compare_image_to_hash_library(self, item, fig, result_dir, summary=None):
        hash_comparison_pass = False
        if summary is None:
            summary = {}

        compare = self.get_compare(item)
        savefig_kwargs = compare.kwargs.get('savefig_kwargs', {})

        if not self.results_hash_library_name:
            # Use hash library name of current test as results hash library name
            self.results_hash_library_name = Path(compare.kwargs.get("hash_library", "")).name

        hash_library_filename = self.hash_library or compare.kwargs.get('hash_library', None)
        hash_library_filename = (Path(item.fspath).parent / hash_library_filename).absolute()

        if not Path(hash_library_filename).exists():
            pytest.fail(f"Can't find hash library at path {hash_library_filename}")

        hash_library = self.load_hash_library(hash_library_filename)
        hash_name = self.generate_test_name(item)
        baseline_hash = hash_library.get(hash_name, None)
        summary['baseline_hash'] = baseline_hash

        test_hash = self.generate_image_hash(item, fig)
        summary['result_hash'] = test_hash

        if baseline_hash is None:  # hash-missing
            summary['status'] = 'failed'
            summary['hash_status'] = 'missing'
            summary['status_msg'] = (f"Hash for test '{hash_name}' not found in {hash_library_filename}. "
                                     f"Generated hash is {test_hash}.")
        elif self.kernel.equivalent_hash(test_hash, baseline_hash):  # hash-match
            hash_comparison_pass = True
            summary['status'] = 'passed'
            summary['hash_status'] = 'match'
            summary['status_msg'] = 'Test hash matches baseline hash.'
            self.kernel.update_summary(summary)
        else:  # hash-diff
            summary['status'] = 'failed'
            summary['hash_status'] = 'diff'
            summary['status_msg'] = (f"Hash {test_hash} doesn't match hash "
                                     f"{baseline_hash} in library "
                                     f"{hash_library_filename} for test {hash_name}.")
            self.kernel.update_summary(summary)

        # Save the figure for later summary (will be removed later if not needed)
        test_image = (result_dir / "result.png").absolute()
        fig.savefig(str(test_image), **savefig_kwargs)
        summary['result_image'] = test_image.relative_to(self.results_dir).as_posix()

        # Hybrid mode (hash and image comparison)
        if self.baseline_directory_specified(item):

            # Skip image comparison if hash matches (unless `--mpl-results-always`)
            if hash_comparison_pass and not self.results_always:
                return

            # Run image comparison
            baseline_summary = {}  # summary for image comparison to merge with hash comparison summary
            try:  # Ignore all errors as success does not influence the overall test result
                baseline_comparison = self.compare_image_to_baseline(item, fig, result_dir,
                                                                     summary=baseline_summary)
            except Exception as baseline_error:  # Append to test error later
                baseline_comparison = str(baseline_error)
            else:  # Update main summary
                for k in ['image_status', 'baseline_image', 'diff_image',
                          'rms', 'tolerance', 'result_image']:
                    summary[k] = summary[k] or baseline_summary.get(k)

            # Append the log from image comparison
            r = baseline_comparison or "The comparison to the baseline image succeeded."
            summary['status_msg'] += ("\n\n"
                                      "Image comparison test\n"
                                      "---------------------\n") + r

        if hash_comparison_pass:  # Return None to indicate test passed
            return
        return summary['status_msg']

    def pytest_runtest_setup(self, item):  # noqa

        compare = self.get_compare(item)

        if compare is None:
            return

        import matplotlib.pyplot as plt
        try:
            from matplotlib.testing.decorators import remove_ticks_and_titles
        except ImportError:
            from matplotlib.testing.decorators import ImageComparisonTest as MplImageComparisonTest
            remove_ticks_and_titles = MplImageComparisonTest.remove_text

        style = compare.kwargs.get('style', DEFAULT_STYLE)
        remove_text = compare.kwargs.get('remove_text', DEFAULT_REMOVE_TEXT)
        backend = compare.kwargs.get('backend', DEFAULT_BACKEND)

        original = item.function

        @wraps(item.function)
        def item_function_wrapper(*args, **kwargs):

            with plt.style.context(style, after_reset=True), switch_backend(backend):

                # Run test and get figure object
                if inspect.ismethod(original):  # method
                    # In some cases, for example if setup_method is used,
                    # original appears to belong to an instance of the test
                    # class that is not the same as args[0], and args[0] is the
                    # one that has the correct attributes set up from setup_method
                    # so we ignore original.__self__ and use args[0] instead.
                    fig = original.__func__(*args, **kwargs)
                else:  # function
                    fig = original(*args, **kwargs)

                if remove_text:
                    remove_ticks_and_titles(fig)

                test_name = self.generate_test_name(item)
                result_dir = self.make_test_results_dir(item)

                summary = {
                    'status': None,
                    'image_status': None,
                    'hash_status': None,
                    'status_msg': None,
                    'baseline_image': None,
                    'diff_image': None,
                    'rms': None,
                    'tolerance': None,
                    'result_image': None,
                    'baseline_hash': None,
                    'result_hash': None,
                }

                # What we do now depends on whether we are generating the
                # reference images or simply running the test.
                if self.generate_dir is not None:
                    summary['status'] = 'skipped'
                    summary['image_status'] = 'generated'
                    summary['status_msg'] = 'Skipped test, since generating image.'
                    generate_image = self.generate_baseline_image(item, fig)
                    if self.results_always:  # Make baseline image available in HTML
                        result_image = (result_dir / "baseline.png").absolute()
                        shutil.copy(generate_image, result_image)
                        summary['baseline_image'] = \
                            result_image.relative_to(self.results_dir).as_posix()

                if self.generate_hash_library is not None:
                    summary['hash_status'] = 'generated'
                    image_hash = self.generate_image_hash(item, fig)
                    self._generated_hash_library[test_name] = image_hash
                    summary['baseline_hash'] = image_hash

                # Only test figures if not generating images
                if self.generate_dir is None:
                    # Compare to hash library
                    if self.hash_library or compare.kwargs.get('hash_library', None):
                        msg = self.compare_image_to_hash_library(item, fig, result_dir, summary=summary)

                    # Compare against a baseline if specified
                    else:
                        msg = self.compare_image_to_baseline(item, fig, result_dir, summary=summary)

                    close_mpl_figure(fig)

                    if msg is None:
                        if not self.results_always:
                            shutil.rmtree(result_dir)
                            for image_type in ['baseline_image', 'diff_image', 'result_image']:
                                summary[image_type] = None  # image no longer exists
                    else:
                        self._test_results[test_name] = summary
                        pytest.fail(msg, pytrace=False)

                close_mpl_figure(fig)

                self._test_results[test_name] = summary

                if summary['status'] == 'skipped':
                    pytest.skip(summary['status_msg'])

        if item.cls is not None:
            setattr(item.cls, item.function.__name__, item_function_wrapper)
        else:
            item.obj = item_function_wrapper

    def generate_summary_json(self):
        json_file = self.results_dir / 'results.json'
        with open(json_file, 'w') as f:
            json.dump(self._test_results, f, indent=2)
        return json_file

    def pytest_unconfigure(self, config):
        """
        Save out the hash library at the end of the run.
        """
        result_hash_library = self.results_dir / (self.results_hash_library_name or "temp.json")
        if self.generate_hash_library is not None:
            hash_library_path = Path(config.rootdir) / self.generate_hash_library
            hash_library_path.parent.mkdir(parents=True, exist_ok=True)
            with open(hash_library_path, "w") as fp:
                json.dump(self._generated_hash_library, fp, indent=2)
            if self.results_always:  # Make accessible in results directory
                # Use same name as generated
                result_hash_library = self.results_dir / hash_library_path.name
                shutil.copy(hash_library_path, result_hash_library)
        elif self.results_always and self.results_hash_library_name:
            result_hashes = {k: v['result_hash'] for k, v in self._test_results.items()
                             if v['result_hash']}
            if len(result_hashes) > 0:  # At least one hash comparison test
                with open(result_hash_library, "w") as fp:
                    json.dump(result_hashes, fp, indent=2)

        if self.generate_summary:
            kwargs = {}
            if 'json' in self.generate_summary:
                summary = self.generate_summary_json()
                print(f"A JSON report can be found at: {summary}")
            if result_hash_library.exists():  # link to it in the HTML
                kwargs["hash_library"] = result_hash_library.name
            if 'html' in self.generate_summary:
                summary = generate_summary_html(self._test_results, self.results_dir, **kwargs)
                print(f"A summary of test results can be found at: {summary}")
            if 'basic-html' in self.generate_summary:
                summary = generate_summary_basic_html(self._test_results, self.results_dir,
                                                      **kwargs)
                print(f"A summary of test results can be found at: {summary}")


class FigureCloser:
    """
    This is used in place of ImageComparison when the --mpl option is not used,
    to make sure that we still close figures returned by tests.
    """

    def __init__(self, config):
        self.config = config

    def pytest_runtest_setup(self, item):

        compare = get_marker(item, DEFAULT_MARKER)

        if compare is None:
            return

        original = item.function

        @wraps(item.function)
        def item_function_wrapper(*args, **kwargs):

            if inspect.ismethod(original):  # method
                fig = original.__func__(*args, **kwargs)
            else:  # function
                fig = original(*args, **kwargs)

            close_mpl_figure(fig)

        if item.cls is not None:
            setattr(item.cls, item.function.__name__, item_function_wrapper)
        else:
            item.obj = item_function_wrapper
