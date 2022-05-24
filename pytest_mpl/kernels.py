"""
This module contains the supported hashing kernel implementations.

"""
import hashlib
from abc import ABC, abstractmethod

import imagehash
from PIL import Image

#: The default hamming distance bit tolerance for "similar" imagehash hashes.
DEFAULT_HAMMING_TOLERANCE = 2

#: The default imagehash hash size (N), resulting in a hash of N**2 bits.
DEFAULT_HASH_SIZE = 16

#: Level of image detail (high) or structure (low) represented by phash .
DEFAULT_HIGH_FREQUENCY_FACTOR = 4

#: Registered kernel names.
KERNEL_PHASH = "phash"
KERNEL_SHA256 = "sha256"

__all__ = [
    "DEFAULT_HAMMING_TOLERANCE",
    "DEFAULT_HASH_SIZE",
    "DEFAULT_HIGH_FREQUENCY_FACTOR",
    "KERNEL_PHASH",
    "KERNEL_SHA256",
    "KernelPHash",
    "KernelSHA256",
    "kernel_factory",
]


class Kernel(ABC):
    """
    Kernel abstract base class (ABC) which defines a simple common kernel API.

    """

    def __init__(self, plugin):
        # Containment of the plugin allows the kernel to cherry-pick required state.
        self._plugin = plugin

    @abstractmethod
    def equivalent_hash(self, result, baseline, marker=None):
        """
        Determine whether the kernel considers the provided result (actual)
        and baseline (expected) hashes as similar.

        Parameters
        ----------
        result : str
            The hash of the image generated by the test.
        baseline : str
            The hash of the baseline image.
        marker : pytest.Mark
            The test marker, which may contain kwarg options to be
            applied to the equivalence test.

        Returns
        -------
        bool
            Whether the result and baseline hashes are deemed similar.

        """

    @abstractmethod
    def generate_hash(self, buffer):
        """
        Computes the hash of the image from the in-memory/open byte stream
        buffer.

        Parameters
        ----------
        buffer : stream
            The in-memory/open byte stream of the image.

        Returns
        -------
        str
            The string representation (hexdigest) of the image hash.

        """

    def update_status(self, message):
        """
        Append the kernel status message to the provided message.

        Parameters
        ----------
        message : str
            The existing status message.

        Returns
        -------
        str
            The updated status message.

        """
        return message

    def update_summary(self, summary):
        """
        Refresh the image comparison summary with relevant kernel entries.

        Parameters
        ----------
        summary : dict
            Image comparison test report summary.

        Returns
        -------
        None

        """
        # The "name" class property *must* be defined in derived child class.
        summary["kernel"] = self.name


class KernelPHash(Kernel):
    """
    Kernel that calculates a perceptual hash of an image for the
    specified hash size (N) and high frequency factor.

    Where the resultant perceptual hash will be composed of N**2 bits.

    """

    name = KERNEL_PHASH

    def __init__(self, plugin):
        super().__init__(plugin)
        # Keep state of the equivalence result.
        self.equivalent = None
        # Keep state of hash hamming distance (whole number) result.
        self.hamming_distance = None
        # Value may be overridden by py.test marker kwarg.
        arg = self._plugin.hamming_tolerance
        self.hamming_tolerance = arg if arg is not None else DEFAULT_HAMMING_TOLERANCE
        # The hash-size (N) defines the resultant N**2 bits hash size.
        arg = self._plugin.hash_size
        self.hash_size = arg if arg is not None else DEFAULT_HASH_SIZE
        # The level of image detail (high freq) or structure (low freq)
        # represented in perceptual hash thru discrete cosine transform.
        arg = self._plugin.high_freq_factor
        self.high_freq_factor = (
            arg if arg is not None else DEFAULT_HIGH_FREQUENCY_FACTOR
        )
        # py.test marker kwarg.
        self.option = "hamming_tolerance"

    def equivalent_hash(self, result, baseline, marker=None):
        if marker:
            value = marker.kwargs.get(self.option)
            if value is not None:
                # Override with the decorator marker value.
                self.hamming_tolerance = int(value)
        # Convert string hexdigest hashes to imagehash.ImageHash instances.
        result = imagehash.hex_to_hash(result)
        baseline = imagehash.hex_to_hash(baseline)
        # Unlike cryptographic hashes, perceptual hashes can measure the
        # degree of "similarity" through hamming distance bit differences
        # between the hashes.
        self.hamming_distance = result - baseline
        self.equivalent = self.hamming_distance <= self.hamming_tolerance
        return self.equivalent

    def generate_hash(self, buffer):
        buffer.seek(0)
        data = Image.open(buffer)
        phash = imagehash.phash(
            data, hash_size=self.hash_size, highfreq_factor=self.high_freq_factor
        )
        return str(phash)

    def update_status(self, message):
        result = str() if message is None else str(message)
        # Only update the status message for non-equivalent hash comparisons.
        if self.equivalent is False:
            msg = (
                f"Hash hamming distance of {self.hamming_distance} bits > "
                f"hamming tolerance of {self.hamming_tolerance} bits."
            )
            result = f"{message} {msg}" if len(result) else msg
        return result

    def update_summary(self, summary):
        super().update_summary(summary)
        summary["hamming_distance"] = self.hamming_distance
        summary["hamming_tolerance"] = self.hamming_tolerance


class KernelSHA256(Kernel):
    """
    A simple kernel that calculates a 256-bit cryptographic SHA hash
    of an image.

    """

    name = KERNEL_SHA256

    def equivalent_hash(self, result, baseline, marker=None):
        # Simple cryptographic hash binary comparison. Interpretation of
        # the comparison result is that the hashes are either identical or
        # not identical. For non-identical hashes, it is not possible to
        # determine a heuristic of hash "similarity" due to the nature of
        # cryptographic hashes.
        return result == baseline

    def generate_hash(self, buffer):
        buffer.seek(0)
        data = buffer.read()
        hasher = hashlib.sha256()
        hasher.update(data)
        return hasher.hexdigest()


#: Registry of available hashing kernel factories.
kernel_factory = {
    KernelPHash.name: KernelPHash,
    KernelSHA256.name: KernelSHA256,
}
