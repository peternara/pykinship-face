from pathlib import Path
import pandas as pd
import numpy as np
from tqdm.auto import tqdm

import argparse
from sklearn.metrics.pairwise import cosine_similarity
from shutil import copyfile

from src.tools.features import l2_norm
from src.tools.metrics import iou
from glob import glob
from warnings import warn


def track_iou(detections, sigma_l, sigma_h, sigma_iou, t_min):
    """
    Simple IOU based tracker.
    See "High-Speed Tracking-by-Detection Without Using Image Information by E. Bochinski, V. Eiselein, T. Sikora" for
    more information.
    Args:
         detections (list): list of detections per frame, usually generated by util.load_mot
         sigma_l (float): low detection threshold.
         sigma_h (float): high detection threshold.
         sigma_iou (float): IOU threshold.
         t_min (float): minimum track length in frames.
    Returns:
        list: list of tracks.
    """

    tracks_active = []
    tracks_finished = []

    for frame_num, detections_frame in tqdm(
        enumerate(detections, start=1), desc="1st loop"
    ):
        # apply low threshold to detections
        dets = [det for det in detections_frame if det["score"] >= sigma_l]

        updated_tracks = []
        for track in tqdm(tracks_active, desc="2nd loop"):
            if len(dets) > 0:
                # get det with highest iou
                best_match = max(
                    dets, key=lambda x: iou(track["bboxes"][-1], x["bbox"])
                )
                if iou(track["bboxes"][-1], best_match["bbox"]) >= sigma_iou:
                    track["bboxes"].append(best_match["bbox"])
                    track["path"].append(best_match["path"])
                    track["max_score"] = max(track["max_score"], best_match["score"])

                    updated_tracks.append(track)

                    # remove from best matching detection from detections

                    # del dets[dets.index(best_match)]
                    del dets[
                        np.where([d["ids"] == best_match["ids"] for d in dets])[0][0]
                    ]

            # if track was not updated
            if len(updated_tracks) == 0 or track is not updated_tracks[-1]:
                # finish track when the conditions are met
                if track["max_score"] >= sigma_h and len(track["bboxes"]) >= t_min:
                    tracks_finished.append(track)

        # create new tracks
        new_tracks = [
            {
                "bboxes": [det["bbox"]],
                "path": [det["path"]],
                "max_score": det["score"],
                "start_frame": frame_num,
            }
            for det in dets
        ]
        tracks_active = updated_tracks + new_tracks

    # finish all remaining active tracks
    tracks_finished += [
        track
        for track in tracks_active
        if track["max_score"] >= sigma_h and len(track["bboxes"]) >= t_min
    ]

    return tracks_finished


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IOU/V-IOU Tracker demo script")
    parser.add_argument(
        "-v",
        "--visual",
        type=str,
        help="visual tracker for V-IOU. Currently supported are "
        "[BOOSTING, MIL, KCF, KCF2, TLD, MEDIANFLOW, GOTURN, NONE] "
        "see README.md for furthert details",
    )
    parser.add_argument(
        "-hr",
        "--keep_upper_height_ratio",
        type=float,
        default=1.0,
        help="Ratio of height of the object to track to the total height of the object "
        "for visual tracking. e.g. upper 30%%",
    )
    parser.add_argument(
        "-f",
        "--frames_path",
        type=str,
        help="sequence frames with format '/path/to/frames/frame_{:04d}.jpg' where '{:04d}' will "
        "be replaced with the frame id. (zero_padded to 4 digits, use {:05d} for 5 etc.)",
    )
    parser.add_argument(
        "-d",
        "--detection_path",
        type=str,
        default="/Volumes/MyWorld/FIW-MM/clips-faces",
        help="full path to CSV file containing the detections",
    )
    parser.add_argument(
        "-o",
        "--output_path",
        type=str,
        default="/Volumes/MyWorld/FIW-MM/clips-tp-faces",
        help="output path to store the tracking results "
        "(MOT challenge/Visdrone devkit compatible format)",
    )
    parser.add_argument(
        "-s",
        "--save_path",
        type=str,
        default="/Volumes/MyWorld/FIW-MM/clips-tp-faces",
        help="output path to store the tracking results "
        "(MOT challenge/Visdrone devkit compatible format)",
    )
    parser.add_argument(
        "-sl", "--sigma_l", type=float, default=0.6, help="low detection threshold"
    )
    parser.add_argument(
        "-sh", "--sigma_h", type=float, default=0.5, help="high detection threshold"
    )
    parser.add_argument(
        "-si",
        "--sigma_iou",
        type=float,
        default=0.5,
        help="intersection-over-union threshold",
    )
    parser.add_argument(
        "-tm", "--t_min", type=float, default=2, help="minimum track length"
    )
    parser.add_argument(
        "-ttl", "--ttl", type=int, default=1, help="time to live parameter for v-iou"
    )
    parser.add_argument(
        "-nms",
        "--nms",
        type=float,
        default=None,
        help="nms for loading multi-class detections",
    )
    parser.add_argument(
        "-fmt",
        "--format",
        type=str,
        default="motchallenge",
        help="format of the detections [motchallenge, visdrone]",
    )

    args = parser.parse_args()
    assert not args.visual or args.visual and args.frames_path, (
        "visual tracking requires video frames, " "please specify via --frames_path"
    )

    assert (
        0.0 < args.keep_upper_height_ratio <= 1.0
    ), "only values between 0 and 1 are allowed"
    assert (
        args.nms is None or 0.0 <= args.nms <= 1.0
    ), "only values between 0 and 1 are allowed"

    path_data = Path(args.detection_path).resolve()

    # path_mids = path_data / "FIDs-MM/visual/image"
    path_mids = path_data
    path_detections = path_data  # / "interm/visual/video-frame-faces/"
    path_encodings = (Path("../../data/fiw-mm") / "features/image/arcface/").resolve()

    path_out = Path(args.output_path).resolve()
    path_save = Path(args.save_path).resolve()
    path_out.mkdir(parents=True, exist_ok=True)
    last_fid_mid = None
    # from os import walk

    df_lut = pd.read_csv(
        "/Users/jrobby/GitHub/pykinship/data/fiw-mm/lists/fiw-videos-master.csv"
    )

    vid_dirs = [p for p in path_detections.glob("F????/v*/v*")]

    fid_vids = [
        (str(p).split("/")[-3], str(p).split("/")[-2], str(p).split("/")[-1])
        for p in vid_dirs
    ]
    # path_detections = path_data / 'interm/visual/video-frame-faces/'
    df_datatable = pd.DataFrame(fid_vids, columns=["fid", "vid", "shot"])
    df_lut["ref"] = df_lut["fid"] + "/MID" + df_lut["mid"].astype(str)
    # df_datatable["ref"] = df_datatable["fid"] + "." + df_datatable["mid"]

    df_datatable.index = df_datatable["vid"].values
    # df_datatable.set_index('vid', inplace=True)
    # df_lut.set_index('vid', inplace=True)
    # df_lut = df_lut.loc[df_datatable.index]
    df_datatable["ref"] = None
    for i, vid in df_lut.iterrows():
        df_datatable.loc[df_datatable.vid.astype(str) == vid["vid"], "ref"] = str(
            vid["ref"]
        )

    df_datatable.sort_values(by=["ref", "vid"], inplace=True)

    umids = df_datatable["ref"].unique()
    umids.sort()
    umids = umids[: int(len(umids) / 2)]
    umids = reversed(umids)
    for xx, mid in enumerate(umids):
        # each video found in nested directories
        # if xx < 40:
        #     continue
        dir_mid = path_encodings / mid

        # obin = path_out / mid
        # obin.mkdir(parents=True, exist_ok=True)
        if not dir_mid.joinpath("encodings.pkl").is_file():
            with open("missing.txt", "a") as ff:
                ff.writelines([(str(dir_mid.joinpath("encodings.pkl")) + "\n")])
            warn(str(mid) + "missing pickle")
            continue
        savebin = path_save / mid
        savebin.mkdir(parents=True, exist_ok=True)

        encodings = pd.read_pickle(dir_mid.joinpath("encodings.pkl"))
        arr_encodings = np.array(list(encodings.values()))
        df_cur = df_datatable.loc[df_datatable.ref == mid]
        nclips = len(df_cur)

        all_tracks = []
        for j in np.arange(nclips):
            try:
                detection_path = (
                    path_detections
                    / df_cur.iloc[j]["fid"]
                    / df_cur.iloc[j]["vid"]
                    / df_cur.iloc[j]["shot"]
                )
                obin = path_out / mid / df_cur.iloc[j]["vid"] / df_cur.iloc[j]["shot"]
                obin.mkdir(parents=True, exist_ok=True)

                f_tracks = obin.joinpath(detection_path.name + "-tracks").with_suffix(
                    ".pkl"
                )
                if f_tracks.is_file():
                    print("skipping", str(f_tracks), str(f_tracks.stat()))
                    continue

                paths_encodings = list(detection_path.glob("*.npy"))

                if len(glob(str(detection_path) + "/*.npy")) == 0:
                    warn(detection_path, "no features")
                    continue
                paths_faces = [
                    str(p.with_suffix("")) + ".jpg"
                    for p in paths_encodings
                    if Path(str(p.with_suffix("")) + ".jpg").is_file()
                ]
                paths_bb = [str(p.with_suffix("")) + "-bb.csv" for p in paths_encodings]
                frame_id = [int(str(Path(p).name).split("-")[1]) for p in paths_bb]
                bbs = [np.loadtxt(p, delimiter=",", dtype=float) for p in paths_bb]

                frame_ids = np.unique(frame_id)
                frame_id.sort()

                df_detections = pd.DataFrame(
                    (
                        (fid, bb[:-1], bb[-1], p)
                        for fid, bb, p in zip(frame_id, bbs, paths_encodings)
                    ),
                    columns=["fid", "bbox", "score", "path"],
                )
                max_scores = (
                    df_detections[["fid", "score"]].groupby("fid").max()["score"]
                )
                max_scores = max_scores.reset_index()
                max_scores.columns = [max_scores.columns[0], "max_score"]
                df_detections = df_detections.merge(max_scores)
                df_detections["ttl"] = args.ttl
                df_detections["visual_tracker"] = None
                df_detections["ids"] = np.arange(len(df_detections))
                detections = {}
                # df_cur.fid = df_cur.fid.astype(int)
                all_detections = []
                for fid in df_detections.fid.unique():
                    df = df_detections.loc[df_detections.fid == fid]
                    detections = df.to_dict()

                    detections["bbox"] = [v for v in detections["bbox"].values()]
                    detections["path"] = [v for v in detections["path"].values()]
                    detections["score"] = [v for v in detections["score"].values()]
                    detections["ttl"] = [v for v in detections["ttl"].values()]
                    detections["visual_tracker"] = [
                        v for v in detections["visual_tracker"].values()
                    ]
                    detections["fid"] = [v for v in detections["fid"].values()]
                    detections["max_score"] = [
                        v for v in detections["max_score"].values()
                    ]
                    dprocessed = [
                        dict(zip(detections, t)) for t in zip(*detections.values())
                    ]
                    all_detections.append(dprocessed)
                tracks = track_iou(
                    all_detections,
                    args.sigma_l,
                    args.sigma_h,
                    args.sigma_iou,
                    args.t_min,
                )

                scores = []
                ave_encodings = []
                for track in tracks:
                    # compare each track to mid
                    t_paths = track["path"]
                    en = [np.load(f) for f in t_paths]
                    en = np.array(en)
                    en_pooled = np.mean(en, axis=1)
                    en_pooled = en_pooled[..., np.newaxis].T
                    ave_encodings.append(en_pooled)
                    cscores = cosine_similarity(arr_encodings, en)
                    score = np.mean(cscores)
                    scores.append(score)

                pd.to_pickle(tracks, f_tracks)

                idmax = np.argmax(scores)
                top_score = scores[idmax]
                if top_score > 0.32:
                    track = tracks[idmax]
                    mean_encoding = l2_norm(ave_encodings[idmax])
                    dout = obin.joinpath(detection_path.name)
                    dout.mkdir(exist_ok=True, parents=True)
                    fout = dout.joinpath("encoding").with_suffix(".npy")
                    np.save(fout, mean_encoding)
                    impaths = [
                        str(f).replace(".npy", ".jpg")
                        for f in track["path"]
                        if Path(str(f).replace(".npy", ".jpg")).is_file()
                    ]
                    _ = [
                        copyfile(impath, dout.joinpath(impath.split("/")[-1]))
                        for impath in impaths
                    ]
                    _ = [
                        copyfile(str(impath), dout.joinpath(str(impath).split("/")[-1]))
                        for impath in track["path"]
                    ]
                    bbpaths = [
                        str(f).replace(".npy", "-bb.csv")
                        for f in track["path"]
                        if Path(str(f).replace(".npy", "-bb.csv")).is_file()
                    ]
                    _ = [
                        copyfile(str(bbpath), dout.joinpath(str(bbpath).split("/")[-1]))
                        for bbpath in bbpaths
                    ]
                break
            except Exception:
                with open("error.txt", "a") as ff:
                    ff.writelines([(str(detection_path) + "\n")])
                warn("ERROR")
            # all_tracks.append(tracks)
# 0009/MID1/v00010/v00010-001/v00010-001-tracks.pkl
# F0008/MID1/v00001/v00001-022/v00001-022-
# es/F0009/MID2/v00006/v00006-009/v00006-009-track
# s.pkl
# -faces/F0009/MID3/v00008/v00008-002/v00008-002-tracks.pkl
# MID6/v00005/v00005/v00005-tracks.pkl
# 009/MID7/v00004/v00004-064/v00004-064-tracks.pkl
# s/F0012/MID1/v00014/v00014-042/v00014-042-tracks.pkl o
# /F0012/MID1/v00015/v00015-003/v00015-003-tracks.pkl os.s
