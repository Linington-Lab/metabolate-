import gc
import json
import logging
import os
from joblib import Parallel, delayed
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from npanalyst import activity, utils
from npanalyst.utils import PATH


def load_config(config_path: Optional[PATH] = None) -> Dict:
    """loads the config_path config file and stores a bunch of values as globals
        config_path (str, optional): Defaults to 'default.cfg'.
            path to the config file, default can be overridden.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent.joinpath("default.json")

    try:
        with open(config_path) as f:
            config = json.load(f)
    except OSError as e:
        logging.error(e)
        raise e

    configd = {}
    MS1COLS = config["MSFileInfo"]["MS1Cols"].split(",")
    configd["MS1COLS"] = MS1COLS
    MS1COLSTOMATCH = config["MSFileInfo"]["MS1ColsToMatch"].split(",")
    configd["MS1COLSTOMATCH"] = MS1COLSTOMATCH

    try:
        MS2COLS = config["MSFileInfo"]["MS2Cols"].split(",")
        configd["MS2COLS"] = MS2COLS
        MS2COLSTOMATCH = config["MSFileInfo"]["MS2ColsToMatch"].split(",")
        configd["MS2COLSTOMATCH"] = MS2COLSTOMATCH
        MS2ERRORCOLS = utils._make_error_col_names(MS2COLSTOMATCH)
        configd["MS2ERRORCOLS"] = MS2ERRORCOLS
    except KeyError:
        pass

    ERRORINFO = {}
    for name, tup in config["Tolerances"].items():
        etype, ev = tup.split(",")
        if etype == "None":
            etype = None
        if ev == "None":
            ev = None
        else:
            ev = float(ev)
        ERRORINFO[name] = (etype, ev)
    configd["ERRORINFO"] = ERRORINFO
    FILENAMECOL = config["MSFileInfo"]["FileNameCol"]
    configd["FILENAMECOL"] = FILENAMECOL
    MS1ERRORCOLS = utils._make_error_col_names(MS1COLSTOMATCH)
    configd["MS1ERRORCOLS"] = MS1ERRORCOLS

    configd["CALCBASKETINFO"] = config["BasketInfo"]["CalcBasketInfo"]
    configd["BASKETMSLEVEL"] = int(config["BasketInfo"]["BasketMSLevel"])
    configd["MINREPS"] = int(config["ReplicateInfo"]["RequiredReplicates"])
    configd["MSLEVEL"] = int(config["MSFileInfo"]["MSLevel"])

    # Network information
    configd["ACTIVITYTHRESHOLD"] = float(config["NetworkInfo"]["ActivityThreshold"])
    configd["CLUSTERTHRESHOLD"] = float(config["NetworkInfo"]["ClusterThreshold"])

    logging.debug(f"Config loaded: \n{json.dumps(configd, indent=2)}")
    return configd


def _proc_one(sample: str, df_paths: List[Path], configd: Dict, datadir: Path) -> str:
    """
    Process one replica sample. The replicated file is saved as ./Replicated/<sample>_Replicated.csv

    Args:
        sample (str): sample name
        df_paths (list): list of paths to replica files to be loaded

    Returns:
        str: "DONE" when completed
    """

    FILENAMECOL = configd["FILENAMECOL"]
    MS1COLSTOMATCH = configd["MS1COLSTOMATCH"]
    MS1ERRORCOLS = configd["MS1ERRORCOLS"]
    ERRORINFO = configd["ERRORINFO"]
    # calc_basket_info = configd['CalcBasketInfo']

    logging.debug(df_paths)
    logging.debug(";".join(map(str, [MS1ERRORCOLS, ERRORINFO])))

    if df_paths[0].suffix.lower().endswith("csv"):
        dfs = [utils.reduce_to_ms1(pd.read_csv(p), configd) for p in df_paths]
    else:  # mzML data
        dfs = [
            utils.mzml_to_df(p, configd) for p in df_paths
        ]  # assumes only MS1 data is present

    df = pd.concat(dfs, sort=True).reset_index(drop=True)
    # logging.debug(f"{df.head()}")
    utils.gen_error_cols(df, MS1COLSTOMATCH, ERRORINFO)
    # logging.debug(f"{df.head()}")
    rtree = utils.build_rtree(df, MS1ERRORCOLS)
    con_comps = utils.gen_con_comps(rtree, utils.get_rects(df, MS1ERRORCOLS))
    ndf = utils.proc_con_comps(con_comps, df, configd, configd["MINREPS"])
    if configd["MSLEVEL"] == 2:
        ndf["MS2Info"] = [ms2df.to_json() for ms2df in ndf["MS2Info"]]
    logging.debug(f"{ndf.head()}")
    ndf.to_csv(datadir.joinpath("Replicated").joinpath(f"{sample}_Replicated.csv"))
    gc.collect()  # attempt to fix rtree index memory leak...
    logging.debug(f"{sample} done processing!")
    return "DONE"


def proc_folder(datadir: PATH, configd: Dict, max_workers: int = -1) -> None:
    """
    multi proccesor version of proc_folder. by default will use cpu_count workers.

    process a folder of sample data replicates. output files will be saved in ./Replicated

    Args:
        datadir (str): data direcory of sample replicates
        calc_basket_info (bool, optional): Defaults to False. Bool on whether or
            not to save bin info as json strings.
        max_workers (int, optional): Defaults to None. If provided will use that
            many workers for processing. If there is limited system memory this might be good to set low.
    """

    datadir.joinpath("Replicated").mkdir(exist_ok=True)

    paths_iter = utils.gen_rep_df_paths(datadir)
    Parallel(n_jobs=max_workers)(
        delayed(_proc_one)(sample, paths, configd, datadir)
        for sample, paths in paths_iter
    )


def basket(datadir: PATH, configd: Dict) -> None:
    """
    Basket all the replicates in a directory in to a single file called Baskted.csv in datadir
    Unique file names are kept and deliminated with a '|'

    Args:
        datadir (str or Path): the directory of replicated files.
    """
    datadir = Path(datadir)
    FILENAMECOL = configd["FILENAMECOL"]
    MS1COLS = configd["MS1COLS"]
    MS1ERRORCOLS = configd["MS1ERRORCOLS"]
    ERRORINFO = configd["ERRORINFO"]
    ms2 = configd["BASKETMSLEVEL"] == 2
    logging.info("Loading Rep Files")
    df = utils.make_repdf(datadir)
    orig_len = df.shape[0]
    # if ms2:  # de-serialize the json df's w/ multiproc
    #     with ProcessPoolExecutor() as ex:
    #         futs = [
    #             ex.submit(utils._read_json, ms2json, i)
    #             for i, ms2json in enumerate(df["MS2Info"])
    #         ]
    #     ms2dfs = []
    #     for f in tqdm(as_completed(futs), total=orig_len):
    #         ms2dfs.append(f.result())
    #     ms2dfs.sort(key=lambda x: x[0])
    #     df["MS2Info"] = [x[1] for x in ms2dfs]

    # need to handle multiple file name cols from legacy/mixed input files
    df[FILENAMECOL] = np.where(df[FILENAMECOL].isnull(), df["Sample"], df[FILENAMECOL])
    df.dropna(subset=[FILENAMECOL], inplace=True)
    logging.info(f"Dropped {orig_len-df.shape[0]} rows missing values in {FILENAMECOL}")
    utils.gen_error_cols(df, configd["MS1COLSTOMATCH"], ERRORINFO)
    logging.info("Making Rtree Index")
    rtree = utils.build_rtree(df, MS1ERRORCOLS)
    logging.info("Generating Baskets")
    con_comps = utils.gen_con_comps(rtree, utils.get_rects(df, MS1ERRORCOLS), pbar=True)
    ndf = utils.proc_con_comps(con_comps, df, configd, min_reps=1, ms2=ms2)
    #     ndf['MS2Info'] = [ms2df.to_json(orient='split',index=False) for ms2df in ndf['MS2Info']]
    #     ndf['freq'] = [len(s.split('|')) for s in ndf[FILENAMECOL]]
    ndf["freq"] = ndf[FILENAMECOL].apply(lambda x: len(x.split("|")))
    ndf.to_csv(datadir.joinpath("Basketed.csv"), index=False)


def load_and_generate_act_outputs(basket_path, act_path, configd):
    baskets = activity.load_basket_data(basket_path, configd)
    activity_df = activity.load_activity_data(act_path)
    # Scores comes back as dict for if multiple activity files
    # TODO: eliminate dict
    scores = activity.score_baskets(baskets, activity_df)
    activity.make_bokeh_input(baskets, scores, configd["OUTPUTDIR"])

    if configd["ACTIVITYTHRESHOLD"] == "auto":
        act_thresh = activity.auto_detect_threshold(
            [x.activity for x in scores.get("Activity")]
        )
    else:
        act_thresh = configd["ACTIVITYTHRESHOLD"]
    if configd["CLUSTERTHRESHOLD"] == "auto":
        clust_thresh = activity.auto_detect_threshold(
            [abs(x.cluster) for x in scores.get("Activity")]
        )
    else:
        clust_thresh = configd["CLUSTERTHRESHOLD"]

    activity.make_cytoscape_input(
        baskets,
        scores,
        configd["OUTPUTDIR"],
        act_thresh=act_thresh,
        clust_thresh=clust_thresh,
    )


def setup_logging(verbose: bool = False):
    """setup logging

    Args:
        verbose (bool): If True logging level=DEBUG, else WARNING
    """
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level)
