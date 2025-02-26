import os
import shutil
import tempfile
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
from pandas._testing import assert_series_equal

from npanalyst import cli, configuration


# # Helper functions
def dataframe_assertion(reference_path, test_path):
    """This function reads the respective dataframe and compares
    the two files."""
    result_table = pd.read_csv(reference_path)
    # # This resorting is just a safe-guard to assure that rows are ordered properly and error messages are
    # # due to wrong values, not due to interchanged rows
    result_table.sort_values(
        by=["UniqueFiles", "PrecMz", "RetTime"], ignore_index=True, inplace=True
    )

    test_table = pd.read_csv(Path(test_path))
    test_table.sort_values(
        by=["UniqueFiles", "PrecMz", "RetTime"], ignore_index=True, inplace=True
    )
    assert_series_equal(result_table["UniqueFiles"], test_table["UniqueFiles"])
    assert np.sum(result_table["PrecMz"] - test_table["PrecMz"]) < 0.1
    assert np.sum(result_table["RetTime"] - test_table["RetTime"]) < 0.1


# # Define relative path to input files
HERE = Path(__file__).parent

# mzML files
INPUT_MZML_FILES = HERE / "data/BioMAP_mzml_input.zip"

# Replicate compared basketed CSVs
OUTPUT_FILE_REPLICATED = HERE / "data/replicated_mzml_result.zip"

# Basketed CSV file output
OUTPUT_FILE_BASKETED = HERE / "data/basketed_mzml.csv"


# # Test config settings (ms parameter and AS and CS threshold)
def test_config_parameter():
    """This test shall guarantee that the loaded settings are identical to those, used to
    obtain the reference/ground truth results."""
    configd = configuration.load_config(config_path=None)
    print(configd)
    assert configd["ACTIVITYTHRESHOLD"] == 2
    assert configd["CLUSTERTHRESHOLD"] == 0.3
    assert configd["MINREPSREPLICATES"] == 2
    assert configd["MINREPSBASKETS"] == 1
    assert configd["ERRORINFO"]["PrecMz"] == ("ppm", 30.0)
    assert configd["ERRORINFO"]["RetTime"] == ("window", 0.03)
    assert configd["MININTENSITY"] == 2e3


def test_mzml_replicate_comparison():
    """Test for the replicate comparison step. The BioMAP mzML dataset is used to generate the
    replicate-compared csv files. A full dataframe by dataframe comparison is performed to ensure
    identical csv files."""
    # # Create temporary folder for result and test files
    tmpdir = tempfile.mkdtemp()

    # # Open and extract zip file that contains the 2775 mzML files
    with ZipFile(Path(INPUT_MZML_FILES), "r") as zip:
        zip.extractall(Path(tmpdir, "mzml_files"))

    # # Perform replicate comparison
    cli.run_replicate(
        input_path=Path(tmpdir, "mzml_files"),
        output_path=Path(tmpdir),
        workers=-2,
        verbose=False,
        config=None,
    )

    # # Test length of generated replicated files (=925)
    length = len(os.listdir(Path(tmpdir, "replicated")))
    assert length == 925

    # # Get replicated zip output file with expected output files and extract them
    with ZipFile(Path(OUTPUT_FILE_REPLICATED), "r") as zip:
        zip.extractall(Path(tmpdir, "expected_replicated_results"))

    # # Catch all csv files from the expected results
    replicate_file_names = os.listdir(Path(tmpdir, "expected_replicated_results"))

    # # Compare the expected replicated files with the produced files
    for rep in replicate_file_names:
        dataframe_assertion(
            reference_path=Path(tmpdir, "expected_replicated_results", rep),
            test_path=Path(tmpdir, "replicated", rep),
        )

    # # Remove temporary folder. Windows would not delete all files.
    # # Python 3.11 seems to enable the ignore_errors function also for tempfile.TemporaryDirectory() which
    # # is the nicer context manager option.
    # shutil.rmtree(tmpdir, ignore_errors=True)


def test_mzml_basket_building():
    """Test for basket building step. A folder with expected replicate-compared output CSVs is used
    as the input. The resulting basket.csv file is compared to an expected output file. A full dataframe by dataframe
    comparison is performed to ensure identical csv files."""
    # # Create temporary folder for result and test files
    tmpdir = tempfile.mkdtemp()

    # # Get replicated zip output file with expected output files and extract them
    with ZipFile(Path(OUTPUT_FILE_REPLICATED), "r") as zip:
        zip.extractall(Path(tmpdir))

    cli.run_basketing(
        input_path=Path(tmpdir), output_path=Path(tmpdir), verbose=False, config=None
    )

    # # Compare the expected basketed file with the produced file
    dataframe_assertion(
        reference_path=Path(OUTPUT_FILE_BASKETED),
        test_path=Path(tmpdir, "basketed.csv"),
    )

    # # Remove the temp folder
    shutil.rmtree(tmpdir, ignore_errors=True)


# if __name__ == "__main__":
#
#     start = time.time()
#
#     test_mzml_basket_building()
#
#     test_mzml_replicate_comparison()
#
#     test_mzml_basket_building()
#
#     print(f"This testing took: {(time.time() - start) / 60:.2f} minutes.")
