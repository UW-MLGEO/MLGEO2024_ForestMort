"""ca_tree_mort dataset."""

from google.cloud.storage import Client
import numpy as np

import tensorflow_datasets as tfds
import tensorflow as tf
import dataclasses

# Proto spec as generated by Earth Engine
MAX_LENGTH = 21
MIN_LENGTH = 2
DEFAULT_SPEC = {
  "EVI_p5"       : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "EVI_p50"      : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "EVI_p95"      : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "dT_p5"        : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "dT_p50"       : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "dT_p95"       : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "spei30d_p5"   : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "spei30d_p50"  : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "spei30d_p95"  : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "winter_tmin"  : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "prcp"         : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "latitude"     : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "longitude"    : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32),
  "elevation"    : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.int64),
  "year"         : tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.int64),
  "pct_mortality": tf.io.FixedLenFeature([MAX_LENGTH], dtype=tf.float32) 
}

# Settings for a particular instance of the data.
@dataclasses.dataclass
class DatasetLengthConfig(tfds.core.BuilderConfig):
  time_series_length: int = MIN_LENGTH
  gcs_proj: str = "forest-lst"
  gcs_bucket: str = "forest-lst-test-export"
  gcs_prefix: str = "ca_dense_tensors_v3"

class CaTreeMort(tfds.core.GeneratorBasedBuilder):
  """DatasetBuilder for ca_tree_mort dataset."""

  VERSION = tfds.core.Version('1.0.0')
  RELEASE_NOTES = {
      '1.0.0': 'Initial release.',
  }

  BUILDER_CONFIGS = [
    DatasetLengthConfig(name=str(l) + "_years", description=str(l) + "_years",
                        time_series_length=l) 
    for l in range(MIN_LENGTH, MAX_LENGTH)
  ]

  def _info(self) -> tfds.core.DatasetInfo:
    """Returns the dataset metadata."""
    # Essentially duplicates the default spec but updates each tensor shape to reflect
    # this config.
    return self.dataset_info_from_configs(
        features=tfds.features.FeaturesDict({
          key: tfds.features.Tensor(shape=[self.builder_config.time_series_length],
                                    dtype=DEFAULT_SPEC[key].dtype)
          for key in DEFAULT_SPEC
        }),
        supervised_keys=None, 
        homepage="https://github.com/s-kganz/ForestLST"
    )

  def _split_generators(self, dl_manager: tfds.download.DownloadManager):
    """
    Returns SplitGenerators. Note that since all of the data are stored
    as TFRecords in cloud storage, the download manager doesn't actually
    do anything.
    """
    return {
        'everything': self._generate_examples(),
    }

  @staticmethod
  def _decode_proto(proto):
    """Load a serialized TFRecord into memory."""
    return tf.io.parse_single_example(proto, DEFAULT_SPEC)

  @staticmethod
  def _empty_record(record):
    """Determine if pixel has any valid data in it."""
    return tf.math.count_nonzero(record["year"]) > 0
  
  def _apply_windowing(self, example):
    """
    Yield subsequences of example that have consecutive data points equal in length
    to the parameter set in the builder config.
    """
    window_size = self.builder_config.time_series_length
    windows = np.lib.stride_tricks.sliding_window_view(example["year"], window_size)
    start_idxs  = np.where(np.sum(windows > 0, axis=1) == window_size)[0]
    for idx in start_idxs:
        yield {
            key: (example[key][idx:idx+window_size]).numpy() for key in example
        }

  def _generate_examples(self):
    """Yields examples."""
    # TODO(ca_tree_mort): Yields (key, example) tuples from the dataset
    # Find all the TFRecords on GCS
    client = Client(project=self.builder_config.gcs_proj)
    tfrecords = [
      "/".join(["gs://{}".format(self.builder_config.gcs_bucket), f.name])
      for f in client.list_blobs(self.builder_config.gcs_bucket, prefix=self.builder_config.gcs_prefix)
      if ".tfrecord" in f.name
    ]
    assert(len(tfrecords) > 0)

    # Generate a TFRecordDataset, drop empty pixels
    ds = tf.data.TFRecordDataset(tfrecords)\
      .map(self._decode_proto)\
      .filter(self._empty_record)

    # Yield features with windowing
    # TODO actually hash the examples
    for example in ds:
      for windowed_example in self._apply_windowing(example):
        # Key is (pixel lat, pixel lon, first year of example)
        key = (
          windowed_example["latitude"][0], 
          windowed_example["longitude"][0], 
          windowed_example["year"][0]
        )
        yield hash(key), windowed_example