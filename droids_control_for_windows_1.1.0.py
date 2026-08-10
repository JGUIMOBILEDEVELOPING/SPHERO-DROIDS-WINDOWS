#!/usr/bin/env python3
"""Droids Control for Windows — single-file desktop application.

Run:
    python droids_control_for_windows.py

The default mode uses real Bluetooth Low Energy through the ``spherov2``
and ``bleak`` packages. Install: ``pip install spherov2 bleak``.
Test mode without robots: ``python droids_control_for_windows_fixed.py --simulate``.
"""

from __future__ import annotations

import json
import base64
import math
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zlib
from urllib.parse import urlencode
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Set

try:
    import tkinter as tk
except ImportError as exc:  # Friendly message for minimal Linux installations.
    raise SystemExit(
        "Droids Control for Windows requires Tkinter. "
        "Windows/macOS: use a full Python 3 installation. "
        "Linux Debian/Ubuntu: sudo apt install python3-tk"
    ) from exc


# ── Palette ───────────────────────────────────────────────────────────────────
BG = "#060B11"
PANEL = "#0A121B"
PANEL_2 = "#0E1924"
PANEL_3 = "#101F2C"
LINE = "#19384A"
LINE_BRIGHT = "#27617A"
CYAN = "#19D8FF"
BLUE = "#1685FF"
TEXT = "#D9EDF7"
MUTED = "#6E899A"
GREEN = "#55E6B2"
AMBER = "#FFBD68"
RED = "#FF527B"


class TechScrollbar(tk.Canvas):
    """Windows-theme-independent dark-blue 3D vertical scrollbar."""

    def __init__(self, master, command=None, orient="vertical", **options):
        if orient != "vertical":
            raise ValueError("TechScrollbar supports vertical orientation only")
        super().__init__(master, width=17, bg="#050E17", bd=0,
                         highlightthickness=1, highlightbackground="#0B2A3C",
                         takefocus=False, **options)
        self.command = command
        self.first, self.last = 0.0, 1.0
        self.drag_offset = None
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _event: setattr(self, "drag_offset", None))

    def configure(self, cnf=None, **options):
        if cnf and isinstance(cnf, dict) and "command" in cnf:
            self.command = cnf["command"]
            cnf = {key: value for key, value in cnf.items() if key != "command"}
        if "command" in options:
            self.command = options.pop("command")
        return super().configure(cnf, **options)

    config = configure

    def set(self, first, last) -> None:
        self.first = max(0.0, min(1.0, float(first)))
        self.last = max(self.first, min(1.0, float(last)))
        self._draw()

    def _geometry(self):
        height = max(40, self.winfo_height())
        top, bottom = 17, height - 17
        track = max(1.0, bottom - top)
        visible = max(0.0, min(1.0, self.last - self.first))
        thumb_height = min(track, max(26.0, track * visible))
        movable = max(0.0, track - thumb_height)
        max_first = max(0.000001, 1.0 - visible)
        thumb_top = top + movable * min(1.0, self.first / max_first)
        return height, top, bottom, thumb_top, thumb_top + thumb_height, movable, max_first

    def _draw(self) -> None:
        self.delete("all")
        width = max(14, self.winfo_width())
        height, top, bottom, thumb_top, thumb_bottom, _movable, _max_first = self._geometry()
        self.create_rectangle(1, 1, width - 2, height - 2, fill="#050E17", outline="#123E5A")
        self.create_rectangle(4, top, width - 5, bottom, fill="#071725", outline="#02080E")
        self.create_line(3, top, 3, bottom, fill="#174C68")
        for y1, y2, upward in ((2, 16, True), (height - 17, height - 3, False)):
            self.create_rectangle(2, y1, width - 3, y2, fill="#0D3047", outline="#02080E")
            self.create_line(3, y1 + 1, width - 4, y1 + 1, fill="#2A6E91")
            cy = (y1 + y2) / 2
            points = ((width / 2, cy - 3, width / 2 - 4, cy + 3, width / 2 + 4, cy + 3)
                      if upward else
                      (width / 2, cy + 3, width / 2 - 4, cy - 3, width / 2 + 4, cy - 3))
            self.create_polygon(points, fill="#28B8E8", outline="#07131D")
        self.create_rectangle(3, thumb_top, width - 4, thumb_bottom,
                              fill="#104566", outline="#1685B8", width=1)
        self.create_line(4, thumb_top + 1, width - 5, thumb_top + 1, fill="#42B9E2")
        self.create_line(4, thumb_top + 1, 4, thumb_bottom - 1, fill="#287FA5")
        self.create_line(width - 4, thumb_top + 1, width - 4, thumb_bottom, fill="#020A10")
        middle = (thumb_top + thumb_bottom) / 2
        for offset in (-3, 0, 3):
            self.create_line(6, middle + offset, width - 7, middle + offset, fill="#2C86AA")

    def _press(self, event) -> None:
        if not self.command:
            return
        _height, top, bottom, thumb_top, thumb_bottom, _movable, _max_first = self._geometry()
        if event.y < top:
            self.command("scroll", -1, "units")
        elif event.y > bottom:
            self.command("scroll", 1, "units")
        elif event.y < thumb_top:
            self.command("scroll", -1, "pages")
        elif event.y > thumb_bottom:
            self.command("scroll", 1, "pages")
        else:
            self.drag_offset = event.y - thumb_top

    def _drag(self, event) -> None:
        if self.drag_offset is None or not self.command:
            return
        _height, top, _bottom, _thumb_top, _thumb_bottom, movable, max_first = self._geometry()
        if movable > 0:
            position = max(0.0, min(movable, event.y - self.drag_offset - top))
            self.command("moveto", (position / movable) * max_first)


def tech_scrollbar(master, **options) -> TechScrollbar:
    return TechScrollbar(master, **options)

CONFIG_PATH = Path.home() / ".droids_control.json"
MACRO_PATH = Path(__file__).resolve().with_name("droids_control_macro.json")
LOG_PATH = Path(__file__).resolve().with_name("droids_control_log.txt")
AUTO_CONNECT_RETRY_LIMIT = 2


def exception_text(exc: BaseException) -> str:
    """Return useful diagnostics even for exceptions with an empty message."""
    message = str(exc).strip()
    details = [f"type={exc.__class__.__name__}", f"message={message or '<empty>'}",
               f"repr={exc!r}"]
    if exc.__cause__ is not None:
        details.append(f"cause={exc.__cause__!r}")
    return ", ".join(details)



@dataclass
class RobotSpec:
    robot_id: str
    model_id: str
    slot: int
    name: str
    robot_type: str
    accent: str
    shape: str


@dataclass
class RobotState:
    spec: RobotSpec
    discovered: bool = False
    connected: bool = False
    connecting: bool = False
    connection_stale: bool = False
    connection_fault: str = ""
    connection_failures: int = 0
    focused: bool = False
    selected: bool = False
    battery: Optional[int] = None
    battery_voltage: Optional[float] = None
    battery_state: Optional[str] = None
    signal: int = 0
    next_confirmation: Optional[float] = None
    timer_job: Optional[str] = None
    animations: Set[str] = field(default_factory=set)


MODEL_DEFINITIONS = (
    ("r2d2", "R2-D2", "ASTROMECH", CYAN, "astro"),
    ("r2q5", "R2-Q5", "IMPERIAL ASTROMECH", RED, "astro"),
    ("bb8e", "BB-8E", "ROLLING UNIT", AMBER, "ball"),
    ("bb9e", "BB-9E", "SECURITY UNIT", BLUE, "ball"),
)

DISPLAY_NAME_OVERRIDES = {
    ("r2d2", 1): "R2-D2",
    ("r2d2", 2): "R2-D6",
    ("r2q5", 1): "R2-Q5",
    ("bb8e", 1): "BB-8 01",
    ("bb8e", 2): "BB-8 02",
    ("bb9e", 1): "BB-9E",
}


def instance_id(model_id: str, slot: int) -> str:
    return model_id if slot == 1 else f"{model_id}_2"


def model_id_for(robot_id: str) -> str:
    return robot_id[:-2] if robot_id.endswith("_2") else robot_id


ROBOT_SPECS = [
    RobotSpec(instance_id(model_id, slot), model_id, slot,
              DISPLAY_NAME_OVERRIDES.get((model_id, slot),
                                         f"{display_name} {slot:02d}"),
              robot_type, accent, shape)
    for model_id, display_name, robot_type, accent, shape in MODEL_DEFINITIONS
    for slot in (1, 2)
]
ROBOT_SPEC_BY_ID = {spec.robot_id: spec for spec in ROBOT_SPECS}
MODEL_IDS = tuple(item[0] for item in MODEL_DEFINITIONS)
ROBOT_RAIL_ORDER = ("r2d2", "r2q5", "r2d2_2", "bb8e", "bb9e", "bb8e_2")

# Firmware IDs recovered from the original Droids by Sphero 1.7.2.4 data.
# This corrects several mislabeled R2-D2 enum members in spherov2 0.12.1 and
# exposes animations omitted by that Python release.
R2D2_FIRMWARE_ANIMATIONS = {
    "CHARGER_1": 0, "CHARGER_2": 1, "CHARGER_3": 2, "CHARGER_4": 3,
    "CHARGER_5": 4, "CHARGER_6": 5, "CHARGER_7": 6,
    "EMOTE_ALARM": 7, "EMOTE_ANGRY": 8, "EMOTE_ANNOYED": 9,
    "EMOTE_CHATTY": 10, "EMOTE_DRIVE": 11, "EMOTE_EXCITED": 12,
    "EMOTE_HAPPY": 13, "EMOTE_ION_BLAST": 14, "EMOTE_LAUGH": 15,
    "EMOTE_NO": 16, "EMOTE_SAD": 17, "EMOTE_SASSY": 18,
    "EMOTE_SCARED": 19, "EMOTE_SPIN": 20, "EMOTE_YES": 21,
    "EMOTE_SCAN": 22, "EMOTE_SLEEP": 23, "EMOTE_SURPRISED": 24,
    "IDLE_1": 25, "IDLE_2": 26, "IDLE_3": 27,
    "PATROL_ALARM": 28, "PATROL_HIT": 29, "PATROL_PATROLLING": 30,
    "WWM_ANGRY": 31, "WWM_ANXIOUS": 32, "WWM_BOW": 33,
    "WWM_CONCERN": 34, "WWM_CURIOUS": 35, "WWM_DOUBLE_TAKE": 36,
    "WWM_EXCITED": 37, "WWM_FIERY": 38, "WWM_FRUSTRATED": 39,
    "WWM_HAPPY": 40, "WWM_JITTERY": 41, "WWM_LAUGH": 42,
    "WWM_LONG_SHAKE": 43, "WWM_NO": 44, "WWM_OMINOUS": 45,
    "WWM_RELIEVED": 46, "WWM_SAD": 47, "WWM_SCARED": 48,
    "WWM_SHAKE": 49, "WWM_SURPRISED": 50, "WWM_TAUNTING": 51,
    "WWM_WHISPER": 52, "WWM_YELLING": 53, "WWM_YOOHOO": 54,
    # Retained for compatibility with the previous catalog.
    "MOTOR": 55,
}

R2Q5_FIRMWARE_ANIMATIONS = {
    "CHARGER_1": 0, "CHARGER_2": 1, "CHARGER_3": 2, "CHARGER_4": 3,
    "CHARGER_5": 4, "CHARGER_6": 5, "CHARGER_7": 6,
    "EMOTE_ALARM": 7, "EMOTE_ANGRY": 8, "EMOTE_ATTENTION": 9,
    "EMOTE_FRUSTRATED": 10, "EMOTE_DRIVE": 11, "EMOTE_EXCITED": 12,
    "EMOTE_SEARCH": 13, "EMOTE_SHORT_CIRCUIT": 14, "EMOTE_LAUGH": 15,
    "EMOTE_NO": 16, "EMOTE_RETREAT": 17, "EMOTE_FIERY": 18,
    "EMOTE_UNDERSTOOD": 19, "EMOTE_YES": 21, "EMOTE_SCAN": 22,
    "EMOTE_SURPRISED": 24, "IDLE_1": 25, "IDLE_2": 26, "IDLE_3": 27,
    "WWM_ANGRY": 31, "WWM_ANXIOUS": 32, "WWM_BOW": 33,
    "WWM_CONCERN": 34, "WWM_CURIOUS": 35, "WWM_DOUBLE_TAKE": 36,
    "WWM_EXCITED": 37, "WWM_FIERY": 38, "WWM_FRUSTRATED": 39,
    "WWM_HAPPY": 40, "WWM_JITTERY": 41, "WWM_LAUGH": 42,
    "WWM_LONG_SHAKE": 43, "WWM_NO": 44, "WWM_OMINOUS": 45,
    "WWM_RELIEVED": 46, "WWM_SAD": 47, "WWM_SCARED": 48,
    "WWM_SHAKE": 49, "WWM_SURPRISED": 50, "WWM_TAUNTING": 51,
    "WWM_WHISPER": 52, "WWM_YELLING": 53, "WWM_YOOHOO": 54,
}

BB9E_FIRMWARE_ANIMATIONS = {
    "EMOTE_ALARM": 0, "EMOTE_NO": 1, "EMOTE_SCAN_SWEEP": 2,
    "EMOTE_SCARED": 3, "EMOTE_YES": 4, "EMOTE_AFFIRMATIVE": 5,
    "EMOTE_AGITATED": 6, "EMOTE_ANGRY": 7, "EMOTE_CONTENT": 8,
    "EMOTE_EXCITED": 9, "EMOTE_FIERY": 10, "EMOTE_GREETINGS": 11,
    "EMOTE_NERVOUS": 12, "EMOTE_RETREAT": 13, "EMOTE_SLEEP": 14,
    "EMOTE_SURPRISED": 15, "EMOTE_UNDERSTOOD": 16, "HIT": 17,
    "WWM_ANGRY": 18, "WWM_ANXIOUS": 19, "WWM_BOW": 20,
    "WWM_CONCERN": 21, "WWM_CURIOUS": 22, "WWM_DOUBLE_TAKE": 23,
    "WWM_EXCITED": 24, "WWM_FIERY": 25, "WWM_HAPPY": 26,
    "WWM_JITTERY": 27, "WWM_LAUGH": 28, "WWM_LONG_SHAKE": 29,
    "WWM_NO": 30, "WWM_OMINOUS": 31, "WWM_RELIEVED": 32,
    "WWM_SAD": 33, "WWM_SCARED": 34, "WWM_SHAKE": 35,
    "WWM_SURPRISED": 36, "WWM_TAUNTING": 37, "WWM_WHISPER": 38,
    "WWM_YELLING": 39, "WWM_YOOHOO": 40, "WWM_FRUSTRATED": 41,
    "IDLE_1": 42, "IDLE_2": 43,
    # Retained from the working previous version. IDLE_3 was disabled in the
    # original app table; EYE_* are firmware exports from spherov2 0.12.1.
    "IDLE_3": 44, "EYE_1": 45, "EYE_2": 46, "EYE_3": 47, "EYE_4": 48,
}

FIRMWARE_ANIMATIONS = {
    "r2d2": R2D2_FIRMWARE_ANIMATIONS,
    "r2q5": R2Q5_FIRMWARE_ANIMATIONS,
    "bb9e": BB9E_FIRMWARE_ANIMATIONS,
}

# Old names are accepted when loading existing macro JSON files, then mapped
# to the corrected original-app name without changing the physical animation.
ANIMATION_ALIASES = {
    "r2d2": {
        "EMOTE_ATTENTION": "EMOTE_ANNOYED", "EMOTE_FRUSTRATED": "EMOTE_CHATTY",
        "EMOTE_SEARCH": "EMOTE_HAPPY", "EMOTE_SHORT_CIRCUIT": "EMOTE_ION_BLAST",
        "EMOTE_RETREAT": "EMOTE_SAD", "EMOTE_FIERY": "EMOTE_SASSY",
        "EMOTE_UNDERSTOOD": "EMOTE_SCARED", "WMM_FRUSTRATED": "WWM_FRUSTRATED",
    },
    "r2q5": {"WMM_FRUSTRATED": "WWM_FRUSTRATED", "WWM_LONGSHAKE": "WWM_LONG_SHAKE"},
    "bb9e": {},
}

# Compact copies of all 51 BB-8 motion paths from the original application.
# Each keyframe is [frame, relative heading, speed]; playback uses 24 FPS and
# sends one combined drive packet per keyframe.
_BB8_PATHS_B85 = "c-qxH+j1Pot^dO3-KoA`{6<l<#F!=(UXnPj6pNx(5@U+g;l(*l%Kv_V?wM`?yBvzLlR9}&6@l)V?FJfm^ypt(FJ7dxH`^z#&wkiF-umWWTkmhrKdtVzzWMP-wNOL(_qWB5uKc~!sX%^&Oo4QrIF9MWrkY3xt!INaj-VSH{)lhC__xK@{pF|C);D@kX|c7sK7V_)dbjn>{q4hQv2}OzaC-rRTfe|ce_ixFyxM#D@`p{kSX!l~#b$;uxHyKfYGES7xbY==guNrA2{7OZ7#0MI^+_{67{mGw?GWPQJN$6@?%gkJkH>#J-9Fmgw8?CTXgqGRf^N{t;1)S(Ip{d(IT*yN=%KKN0)DY{cF;yoh_qOyFc@b>gjWrb&5%8=)MtjvaU|#(POsyl5<ifPM0h-;d9cAw7+92!W^l<ZrURimDyP9}e7Dw_PSg%JuVKR*kYTYj@FO|GyE_)1H_;9%XM**nV{sq0aLXr-ExXe`Z^9yRHemHetl5N(X4A@GN1Lz_gg^sH4MAp<3=dRwnqvJ%Y}km68?Z*{jaai0n>J$eCaks_v2G)l?1D*Y9$bE0N{=3{&yN4+;rw=W_WAt&Ba_#I!^hPsGCmF7PlplLuZIMOXFQyCp$B6pk<xKt-C$LN)57T?R+Wp($LZEg$KN24@}T1MdIRrwelTI0VSzMy$jWt@^=2@6f}I6b2m<9N7|-O8^dwF`uIn=x_m5NR`i2qb+oRLtlcVjE-ItFO$u0s>uZtytG9A)}jR7kul-M9>DGE`2Zq+6$6lZkw``d@R``hz-7@~W7d3gF_fA?hjyWPk4rcwD=-&bM;oaoBJA~Cx|A-~D8=o#Dj?&bmT_vb&Y*sAvr4_+O=o?SG`lNzcmD;5LHc{ZV+VbF%AZ%~11Y$q1gOaKL%I+x%C25SkQv>Jk+o&bvjCpKC_AQXYrSqIXqQkxzM+(ZWakEQk?6tT+y_${%$0A`{iV_oQ04fTO;cFf@~W^bsW8=N+sE1)kX0DmUvNNk`67sgEj4U!G-QWJ=}CxXtM<~wBn;MDYP2fq#a!8Z9T)UvTX9~K&^*Pf3kpFR_L1!CGX&G))cSNq00vxsoECt`piTIL~ilMOO7s75<@3@n7xUHW0RYqSPC76#uoho&kIsVN&UQlLv3ZGtUpNx98;xY$mNa-@w*-LXPOb;}))xNr>H^BIZh+d+gww}hi(rtw&a+ZhXI%ERj>*2S`S*OY1Wi!_)tt-c)hklVwttj4SMc*4~?Gm$>#dTgfECKQC=r^Ud51@Ux|xGvhlDV!dMW#MKUwNg2So!02u1R!361OB{k-dC#YBfpRq05;nIM*@8C_H*?;6F~jC>?uIiQDt0{bHgCdpg<w*7!)aFG=tcfWpA-;jCtfbG8LQH%E%Uo$x@H&$*Y^|562&+n%v!c_4?$+_Wu4RHL1Z$D;0_-zO--zH9?`38XSlinT09^T~A=5fkPE@wL`!`I1_Myvj?NwlocFuOK|1rfFOXRG@PwwB>Rplx&z!}2Xc`-2>Ik|8wbi9Gz}o5H}&L$f(O}vKQQD0AwYJkj!m%Gf<#8c3mgbS#K0=6;$xcPQ-c}HC>0TuKt-bnLv$2jeH4vn)dh%yI2z^K8BnK&q6-bc{x~+dPysB94Vj^l5I|Bi7!-}PfT$B4+yo3#)enbN2T-r@3mGh!ZoWD26f|wemDm6t#uXqz3MDpK;0WuP_ZnB1A3ol{J-@o*G2ZLLqmw7BFj`Wy6;0hO&{1!$nbsxDj?r|}3x+UJI}7SKcZUe*VdA6^jIRpG+wO^=-OQGlNzgb^=WZ9l7TIn$bJ3^WmLV5mzUj8BSP#Q&RUgM#iH?JaBLi}`MWF3-bCrQ9bU0`}4Ausy(+piG>N6W+OW1J6;YchA3F-4Q70BJko7?+!4RXAH_~-rIZ%_0Li&ITjjQTc=nTm;@Zp>3mn$0Y1c;3>^?yheBadx$Oe}AU*#?>96p8E|e%zS}gy}VnL6Sl(bgI7mC%y`z7XC*aL#B<?lCT2SBO9t~{D~2r}cJw@K%36WKv5#--{QAS~FY9|TPjANX&1kV1%{QaPW;EZ7eta`a-BLDW<{0fws{qH2z9&TfGm#HRF`W2tqUM}<BVTq<3Pp_|>B-*C9{jm?crY7(WfuA^Q*I#ao<hE`pE$RMg<pnc{peua7@LK;?c85pTz)>kzr49-n|g8h=e>hhGu!br`3UEtK(_Inf}oFS+S2kYW*$M?+*J-oHa&01Yl62rv&FYJ|G2#Vz&5vYc(Aj3G{dYw59641_LFU+=f5tqfyJF6=bsN-G3=OrbDK`0nMnp*++1I*ZaLd_PLK8uPmkyFK;#q}b|#JAoSP`YWoD8~AULc`o-0^xP8Jl?x8Za`)cg1rA8s#i9(d+`_b)qpvsoO*p~SRIHTjLPfejj_f{f#Xi4KnW=xNY8POQM#O0z*A=oI3Efei+pfI>+RRLf1zPK$_%Phi1FqAN_}3df1rE!c}PAI7vbwGU>jPPEX81wGa**r;Qpj`2o2!5z=p<AWGZoBUoqlLK7+@5QA|mVLXoJ7>-RU#FaY{}j_)O6hmbApM&&M*rn0qTiSkdLjq3mh_QHp5K_%$umh9td*s~c}vnVF%GuX#em}s2LBH87QVrWrCiwCBO7BWB%PldKfS+P33vJR_M12J$|CR-`=ez6!y#TIaQsQ<n3Z5pdaj)VD=!4c2je0<?kn06)a{6|;mn8e2$A7LDP2wrbE-8<>#@O12es0h{JA*e6MQ`X{CNYDj!(b=-<b(<&BB<WX-Z#qV@>MyF$^eUlQ_(3fF1>fW=Pe?dXSnXhPB?OhSZNLAZ#WE1FO3hPIqV)1v6GQd6H2zr9cRl%+j2Qb?~01$T6XXF}H0R35|h`pi`QZ*D4&SP(!M47Izf_i92l6w5Wjl3_uwliOsMc&59+(JL^z?q;5Ch&7i>ACTv+w0t!ZAFf2A)w5lK`VhrXYo7%)OpGIPMQo7e6lWbD43t)7|d@5pWHG|!BI549&#4a!zEL93Ufn(B;l<y*h&+DF(aSKf7HQRue%7S+ERJmtDY1HoU#%4Z1w7|}{_kps<j+NlMvd3g=PoULeXFGfm6Hcnx4$O)IoEJneBQZV++eQe5P?=i;<{=Pe4cuzQnykx)y$xL^Bc@;{FfQPE(+G%Cxik*!K|9!R7M577HqTI}MN*e{_YhrPoGHEh{NYS{#YJ~_`{nVQz1dJKv%)J&`y`f^lEu}`3Yzpm*am&jQ6p0P4Ca(`pPY<E>fA;aBmqz84`#)FFb7f4pl{VGi6RCYWsNbI=+46ZrUbH_LV9zQImg4*`MbN%m)C1tJ2^c%a2^wPkMqtiYyc@~Vzh8>(6I>(2NMT1oBidBy??m9UOK<rlxpnjej?e3>NgBcz<}$p78RO&3pfc_^9P>4_>zNsZ1vyo?!4Ad<u0a$(#W57GccK=2Zn`fJmEtC`XiVW^xzG)-VQUKSlSGdLEkbf#1UE|j6{I`Ns&0Dr5VbD7R=p;0>froY{k<QfkRAI;T6dZruhcRE)q3}Aj@Fte9QQQv4Z)jnNYBJ81sf^`7%hM16v(EY5fBGsoE=0pO!GQxe`Dvf$$O$B_c^g76@=+iUbBy1NGoCG|PY<J=(h7m{Uq;=#DpIp{>wBn8(VzT*~3ZVXUu!CuMz$i^JI2kVqXB!>1l8W4y!>Mk1U<c!>xSkpv>KN1iFCpH>$iN3#~xMoIl5DSh6rKBg4R+)p_$_RaYKjsqMFY2_-g?b3^IZ}svOw%zYUxJ!3J*tf_Tq`knd3p>KiSp!)wup^G6#|64>jXA;>j~UKSBrYL%bfI-r;0Xf<t+lK9>aDaRT;gJD+i}Yh_5uz9jtn-2&VMcgO>u-CIWl&(42wlzOU;hKLT1Z>=t388o;^uGS=6JwSX<%<+RuSWDT@syF3{Gn3_NNCFEv_lX%k`cW4U6@TXnzonA<`n#i{g%y_1vK>>ZqKPdoSwkIp?aVTO5<nmLj@R_BEq@)MvM4NLucp87Q`^{ZJJFH*k*;JXlUIp0QP+lXu%iESe>2Y_!Qvu$Ly4YW**j+$*mi+)GxpFZ~l{QdI&US?tTw@+Wao~vnW$nj&q!)^(*vrR{<2#>WLk&gC!s>ntM#YO}5m7>~M=$I)|U7bIC;LW^;-|rpFR&tD5JYIK+yecDd`0BY#x%qT?E$c8xyZd{)f0`@Aq;-!GFJKs3TeC+*aq7AjA&5Ey2ppBJL0f?xXkODRX4p+(YJRzv;lbZYgqs)$=V~+%cG!4cO+=})56<V#-7hD}?ds}s^|Pog?7iII-9J41ZnkvF1O(1w7*tC=6h)gz9@>n-f6E47+J_^OKv*LX;0Xsmw3`XwNrAhb6U`PF;+>%~ppWC>;RrM$-hr7()CB~`(zWcqE@s`+^ch-h51Vz=%vMVuNY7lsG_M|$xQOXKtifv(i0KVOn65xfBMC;-K@X`_VhrXlfC#Di=%rcT0~tCUZK;*Cki(?0{L&kfXbfZ$MtKB`3koGZnCW2Az68{4(C|Ua1|1*tY>+P~vjt_oAeOPWnp<$X9;?}UthQ{>@j=f910RfRF!4c3AZr+btYHf>d_jgS$nXX6nl=a%XbbO~@z%9i+59C7YCvZTC#hku%a3C)*_S~^yGIMb3LG%Nw4^F#{1nV-SzoM!83+NB?YEZP8pf|-z`VOmB*eloGbmT7cfY}JC09Byr$|^AjA>6fIR0?3;4HW5o53zVpoZ-o-mtHTHL3O<a6}+78*<CMI(Qzlv!VCSfd_=|3EkD!;>8~OC!-@}PmdW|jT>zlT3|o8m#vf<Fg~^<lnvlYLb$J+{07i!C8^p<a^SxLq9=7jxeVB=u>k}k4ZdZgLoP+E=G8|y%#Ot95l%BKp{+V<Y)U6>+;~Z5FfF}>4T@$6v6uLYd1<7IaE!5}*0M-&kTp3XkvY{dV}-J7u!xE<0GoTsLZ#ZxnwsaHP##M=Zp+*zvyKWS$!-VLh?PpLEKqSBmDW*t4TXwSpitv^PwJ^z#}cxIMvh#n<Eb>oPHB|tU&WTW&$d?3RbxtK9oRXul$BEIDXaee{OXRG^_#ah=hv5?&Mx0wt(I3eH$Pooe}JHycT^r7Zf?h3&%mWG%d~l(PkTK0c<fGO{Y{WNP))tNuE9LM;H~d$RsOl1leaflS8FYu+b`z|agO-huy9(j^hR{K2srTulBfAAJ7mw_$(5S<J5!!HZk|Vio|SMt3--tvP@5L5*W;xx-UDwyJl@$pdb%NMEk0`8DE;%K`MQ9;(@8ttvu0>n7@Rpaj<q&A9>nueJRimLNj#q&U(auiWbQ7`Z-vud$%nVm{%s`WBvXQ<iI1ZX;y4L8$;9(fJfFn#c?=W@1vxyiz;yS%zHma|2H6ZOr?IjUQ*+*a6;t+aM4M)1Ou3;jBcp~9-*V8gR0h_GSqg?uMY2+mZYi0s+R-ug<BRJSvo|du`itOV^zMOD#LTVd35_OrQnuf-EjnKHWNs)*DYjq_v#*(!>C3fayob`eyexV}reDOxf<RBUx{}`wq(C3Yy<Jq;Hk1oR?esc5v=yVPVOgV;#agrNjaKER4n(JpRNr+ZZFS%q8jQ;_sjh<`)Qj)6k)=C&;LE^Ika~?OeR-Z<--ic$t9O?&Kp?GWsdmEo9wF;Yx*tHI@CHmv#O7K=vCUhBV8KSxUX{xYdmt+P7633hJEF)ir5$4+T~v;^`6#Ba=;$-a!(mfb&ZN@{d)RoKj-+gj4Cvc~ixrF!^`GE7#uWyU1Rjic&wRW?1rC4v8B_1m#GVbXrG3ElVlJ!zPF?Rv!1Mq(h>@0zbRdl?^|J;VqP6x`)SD0r^%Mg{8s&CYBGv7BtG#z*`f^uN^=B$42uVD8>ACL)TuTrLlMPX45K{Vf-tY<_mL7AbVhJg2?;Qf+(Vm1r<suiB=15Z;%8~A+sA4EdyQB`atD`63F*5;ER6incfUz#(BI%UFBut=XH7cA~MZ-9&!x)-TUc$>UxJ;yE7Dxvwe!T$;13f*X01mxYn2mrVgE0@oVb9^<1ROXFr=7yq^54f>WDHL9eP4Qu>innG)UOhiye_x%-CRH5n6ZR-cf2^43N=A;YueLeE0R|RgISZuBE>jo#6&qItxd^t3hDweN9(moW_^-fo8;CfRVS-~fq`SV(!h}hp!2h>7OD73Al&tvFiOB$z@?2iph9S<VgOXX&sW>4n)Yxs=yP9jY3|}D&g9dhH%EKNU*J+3?hl%^KBO;P!o%(7+sixQm!52&9-KVun#0T`2T}cKo~jr37?un%1vL#0VuCk(0+zKH;>imjvxK=nf4Jrj)XD49?_V6BzM1o(it0tVug9mf<(5SBGQ&}Kqa>7B)$G_5$CHl@=y;)17bQ25&y<csu+CQ3ic$cBQS%pB+0hNFC@@(n`YEwrQJ9p9RWv#(Svyu5B`U0=;yNm=p{!a*nRV3qBJBDi?D{W)6!txapsOHGDD=N_Ez(HhI6tP%kc427>p3#z^D&*!t@t*vV=BLam2{z3Xs~|ctD9JPZk_(BqxJFO)7!h#&-`@l%?8qVxt{vI=zI4Ld62k3T|C$$Ue3^@g%=zkAWAPVlxwuUwO)Wz;=oPv{i`|HOMz$C_Ra^Nu@}jb=5X}inn}m&$4Qe6E%t<`R4(ne+91RSB&!D2Jt^3jC*A)EocQho!Io13CN|d+zw=ZsnvjmsjO*wQ4p@zx6ZLWNqd^KL#qJjxymw&1zwDi+7(OeD31s5ufq$HbplB^ggPakU8^v}+wO6H7QGMMy<q_3xUYN41h^4Al8*;9qLT|_(pX(l+iQ}ZyZ_|`Ef*8n$lw2wM$JnmJdsPr<bX&wzRsb=%X^Hk=b4B$VBuSxrpJ>V(v$lY;q=J!yuc*eF93=*Vk0eo;_!B4>*H9Q&a+JZ=5{b(4L4$s+KzYp1b5snGp}tU(FJ(sOPltOuyW2Y_b05UgYi=7gtMl!Xdc%$B4K0&qyborHz(XLBNt2^R=iAZySE?6n>P1=IL-XnrdD8V{@j61k0R;+{G^y7+O_qIY04V96)9v>2&E>`F{Nnyhd`|0!-TnR9DmTC0t$EJV&t1IagO&|iKIqus{FU<OW(t2<U0unG!~1*Ro;-0SsFXv7>_~eReljI?b%R{emfgtEq%e)%t|1*;7_mYes9V{x{L%0H`BJ{`{dvfCM0AUQ?YEYs5@Pw%h$5AEG|q5v2B+v(e+)>XZl^8V@g_@C)wz5cFAd}ihd2k~+(0r8w%!!6J*GsvkF#9QYt;)cXk0Z-BH(S7+#_+ka%fE^&4ncvw_p*;<psP6Xq6@@tokiOMeB*WN{6N0prKx`Oj1B1crSB}ARr!>Kd;WNF5llT*Ka5N;qX6mT9&{6aan%dd0GB$2WI)-bz*A9MVCyW#_MI}%cJ;NC{415#XK_DVmh%oeWeM8vBY2t9_LAEU*SwKx?Hcq4=%Ny7NgeKV#+l!#+MQYaW>#>BO$lOjn~QAvI^Z8TTDou2h$H?A$elLvNyyuE-Xpsi_Lg}x;a!>;&y1&dO{m-)ESRAlQnJn(CAWi%bFSnT+SiI8s``}&jpF8*Udu7iA*SXJveotez3HnY8O`OH?LWR)dv0ScCt~6oZsKyqP_k54Ob&arw7No2Pa3f$^<`)iy8iQ_Yf%DuchFPhze`G+~sl&2H2dW1Z)Hx@nb6emN)h;Re0G_-`S8$KX~60J577qw$z&#2F^14NPh-vqWH-uW4Z<i<qkpYbH-1o*q&r{>loiK_#v@ir#`fGWMIgsi&)4P7O5C9)2Xco2I7w15ns{;E)TsdQ?48`Ovy4RH&{b+pG*8AZ0w>_ntxHxTH08~F?hSr1NnlZU1C@!GF{$E1tJYio7s|x&4Egq#}7nD()X3=m3I`p5rLgV-i;kq6z4}tXb(D55_lnscO@qfSz*s}zmjP;edq;mb($x>jk~(O7u~}zPXGGX7yH}Cb45gSq(j$q<y)7%L{>hn*{d07KwMWDUJM8RbmZ?&d^C%PF<C`lUOukICWys>?6CLa$Jf}nTwb{WKz||131W%BcTOF}$BkTKvDV}lj3l5DYXn2Ye?gGY$R!pNMt?z)(TX)%!C@;|ESG2nldUA96>GGD&sI>;$|X9%X(!3(#2TI8v=dZxa*0lG+DS4xu|_92?FALRT%s48E{ZR9Eyq5V=>?a)prDt_^McD>k}!z%1;OPYC>SJ{gWz&llV!2SAh;X^1%q5-6kLvygi)+93NA;%<tUdJ1(%~FV-#zQg3C!zG07z+!Q~{$n8X^B;B*pHOmc}ya5_mcX0gUBIGqI*vs_{poX(PrS*$S&PG_mO*EP3P-D*vDE2+Gd)ZR*}Z?&esl~mx0eV}&j_Be&uC0<q<lJ|D>Be!^}FGl+q*>mjrm$qi(|Fbb~!rwmp^PgvLug>r8w|@Qg{{W!(*x&"
BB8_MOTION_PATHS = json.loads(zlib.decompress(base64.b85decode(
    _BB8_PATHS_B85.encode("ascii"))).decode("utf-8"))

SIMULATION_ANIMATION_CATALOG = {
    "r2d2": tuple(R2D2_FIRMWARE_ANIMATIONS),
    "r2q5": tuple(R2Q5_FIRMWARE_ANIMATIONS),
    "bb8e": tuple(BB8_MOTION_PATHS),
    "bb9e": tuple(BB9E_FIRMWARE_ANIMATIONS),
}

# Manual commands are separate from native Animations enums.
# (id, title, description, icon, supported models)
STANDARD_COMMANDS = (
    ("head_left_small", "HEAD LEFT / SMALL", "Dome −30°", "◀", {"r2d2", "r2q5"}),
    ("head_left_large", "HEAD LEFT / LARGE", "Dome −120°", "◀", {"r2d2", "r2q5"}),
    ("head_right_small", "HEAD RIGHT / SMALL", "Dome +30°", "▶", {"r2d2", "r2q5"}),
    ("head_right_large", "HEAD RIGHT / LARGE", "Dome +120°", "▶", {"r2d2", "r2q5"}),
    ("turn_left_small", "TURN LEFT / SMALL", "In-place turn −45°", "↶", {"r2d2", "r2q5", "bb8e", "bb9e"}),
    ("turn_left_large", "TURN LEFT / LARGE", "In-place turn −180°", "↶", {"r2d2", "r2q5", "bb8e", "bb9e"}),
    ("turn_right_small", "TURN RIGHT / SMALL", "In-place turn +45°", "↷", {"r2d2", "r2q5", "bb8e", "bb9e"}),
    ("turn_right_large", "TURN RIGHT / LARGE", "In-place turn +180°", "↷", {"r2d2", "r2q5", "bb8e", "bb9e"}),
)

# These commands are implemented only with set_dome_position and never issue a
# stance or leg command. Native firmware animations intentionally remain
# unclassified because spherov2 exports no leg-behaviour metadata for them.
CENTER_LEG_SAFE_COMMANDS = {
    "head_left_small", "head_left_large", "head_right_small", "head_right_large",
}

# Channels map to real SpheroEduAPI methods. MAIN on R2 is a FRONT+BACK alias,
# so the UI shows independent physical lights instead of a duplicate.
LED_CHANNELS = {
    "r2d2": (("front", "FRONT RGB"), ("logic", "LOGIC DISPLAY"),
              ("back", "BACK RGB"), ("holo", "HOLO PROJECTOR")),
    "r2q5": (("front", "FRONT RGB"), ("logic", "LOGIC DISPLAY"),
              ("back", "BACK RGB"), ("holo", "HOLO PROJECTOR")),
    "bb8e": (),
    "bb9e": (("body", "BODY RGB"), ("aiming", "AIMING LED"), ("dome", "DOME LEDS")),
}

# Used only until a live attitude/heading sample is available. Zero degrees is
# north, 90 east, 180 south and 270 west.
_MODEL_FALLBACK_HEADINGS = {"r2d2": 323.0, "r2q5": 54.0, "bb8e": 205.0, "bb9e": 126.0}
RADAR_FALLBACK_HEADINGS = {
    spec.robot_id: (_MODEL_FALLBACK_HEADINGS[spec.model_id] + (spec.slot - 1) * 14.0) % 360
    for spec in ROBOT_SPECS
}


class SimulationRobotBackend:
    """Working API simulation whose methods mirror the real backend layer."""

    def __init__(self) -> None:
        self.mode_label = "SIMULATION MODE"
        self.is_simulation = True
        self.connected: Set[str] = set()
        self._stopped = False
        self._power_paused = False

    def discover(self) -> List[str]:
        time.sleep(0.65)
        return [spec.robot_id for spec in ROBOT_SPECS]

    def connect(self, robot_id: str) -> bool:
        if self._stopped or self._power_paused:
            return False
        time.sleep(0.35)
        self.connected.add(robot_id)
        return True

    def move_head(self, robot_id: str) -> bool:
        return robot_id in self.connected and not self._stopped

    def play_connection_confirmation(self, robot_id: str) -> str:
        """Mirror the live backend's per-connection random confirmation."""
        if robot_id not in self.connected or self._stopped:
            raise RuntimeError("Robot is not connected")
        animations = [name for name in
                      SIMULATION_ANIMATION_CATALOG.get(model_id_for(robot_id), ())
                      if "SLEEP" not in name and name != "MOTOR"]
        if animations:
            chosen = random.choice(animations)
            if not self.execute_animation(robot_id, chosen):
                raise RuntimeError(f"Simulation rejected animation {chosen}")
            return f"native animation {chosen}"
        command = random.choice(("turn_left_small", "turn_right_small"))
        if not self.execute_standard(robot_id, command):
            raise RuntimeError(f"Simulation rejected fallback movement {command}")
        return command.replace("_", " ").upper()

    def get_animation_catalog(self) -> Dict[str, List[str]]:
        return {robot_id: list(names) for robot_id, names in SIMULATION_ANIMATION_CATALOG.items()}

    def identity(self, robot_id: str) -> str:
        return f"SIM-{robot_id.upper()}"

    def connection_id(self, robot_id: str) -> str:
        return f"SIM-{robot_id.upper()}"

    def connection_details(self, robot_id: str) -> Dict[str, str]:
        return {"name": self.connection_id(robot_id),
                "address": "SIMULATED", "api_id": robot_id}

    def get_battery(self, robot_id: str) -> Dict[str, object]:
        if robot_id not in self.connected or self._stopped:
            raise RuntimeError("Robot is not connected")
        seed = sum(ord(char) for char in robot_id)
        percent = 64 + seed % 31
        return {"percent": percent, "voltage": 3.30 + percent * 0.0085,
                "power_state": "OK", "source": "simulation"}

    def execute_animation(self, robot_id: str, animation_name: str) -> bool:
        return (robot_id in self.connected and not self._stopped and
                animation_name in SIMULATION_ANIMATION_CATALOG.get(model_id_for(robot_id), ()))

    def execute_standard(self, robot_id: str, command_id: str) -> bool:
        supported = next((models for key, _title, _desc, _icon, models in STANDARD_COMMANDS
                          if key == command_id), set())
        return (robot_id in self.connected and model_id_for(robot_id) in supported
                and not self._stopped)

    def set_led(self, robot_id: str, channel: str, enabled: bool) -> bool:
        valid = {key for key, _label in LED_CHANNELS.get(model_id_for(robot_id), ())}
        return robot_id in self.connected and channel in valid and not self._stopped

    def get_orientation(self, robot_id: str) -> Dict[str, Optional[float]]:
        if robot_id not in self.connected or self._stopped:
            raise RuntimeError("Robot is not connected")
        phase = time.monotonic() / 7.0
        base = RADAR_FALLBACK_HEADINGS.get(robot_id, 0.0)
        yaw = (base + math.sin(phase) * 28.0) % 360.0
        return {
            "direction": yaw,
            "heading": yaw,
            "yaw": yaw,
            "pitch": math.sin(phase * 1.7) * 4.0,
            "roll": math.cos(phase * 1.3) * 3.0,
        }

    def stop_all(self, robot_id: str) -> None:
        _ = robot_id

    def disconnect(self, robot_id: str) -> None:
        self.connected.discard(robot_id)

    def forget(self, robot_id: str) -> None:
        self.connected.discard(robot_id)

    def reset_connection(self, robot_id: str) -> None:
        self.connected.discard(robot_id)

    def pause_for_system_sleep(self) -> None:
        self._power_paused = True

    def resume_after_system_sleep(self) -> None:
        self._power_paused = False

    def finalize_shutdown(self) -> None:
        self._stopped = True

    def shutdown(self) -> None:
        self._stopped = True


class SpheroV2Backend:
    """Real BLE backend for Sphero R2-D2, R2-Q5, BB-8, and BB-9E.

    Imports are delayed until scanning so the GUI can show a readable
    installation error instead of terminating during startup.
    """

    def __init__(self) -> None:
        self.mode_label = "LIVE BLUETOOTH"
        self.is_simulation = False
        self._toys: Dict[str, object] = {}
        self._apis: Dict[str, object] = {}
        self._types: Dict[str, type] = {}
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._robot_locks = {spec.robot_id: threading.RLock() for spec in ROBOT_SPECS}
        self._scan_reports: Dict[str, str] = {}
        self._known_devices: Dict[str, Dict[str, str]] = {}
        self._stopped = False
        self._power_paused = False
        self._loaded = False

    def _load_library(self) -> None:
        if self._loaded:
            return
        try:
            import asyncio
            import bleak
            from bleak.backends.device import BLEDevice
            from spherov2 import scanner
            from spherov2.commands.animatronic import Animatronic
            from spherov2.helper import to_bytes
            from spherov2.sphero_edu import SpheroEduAPI
            from spherov2.toy.bb8 import BB8
            from spherov2.toy.bb9e import BB9E
            from spherov2.toy.r2d2 import R2D2
            from spherov2.toy.r2q5 import R2Q5
            from spherov2.types import Color
            from spherov2.utils import ToyUtil
        except ImportError as exc:
            raise RuntimeError(
                "BLE libraries are missing. Run: pip install spherov2 bleak"
            ) from exc

        class AwakeDisconnectSpheroEduAPI(SpheroEduAPI):
            """Close BLE cleanly without putting the physical droid to sleep.

            spherov2 0.12.1 calls ``ToyUtil.sleep()`` from
            ``SpheroEduAPI.__exit__``.  That makes a normally closed or reset
            application unable to discover the droid again until another app
            wakes it.  The pinned library keeps these three attributes on the
            API object; reproducing its cleanup without the sleep command
            preserves the background-thread and BLE teardown.
            """

            def __exit__(self, *args):
                if getattr(self, "_awake_disconnect_complete", False):
                    return None
                self._awake_disconnect_complete = True
                stopped = getattr(self, "_SpheroEduAPI__stopped")
                worker = getattr(self, "_SpheroEduAPI__thread")
                toy = getattr(self, "_SpheroEduAPI__toy")
                stopped.set()
                if (worker is not None and worker is not threading.current_thread()
                        and worker.is_alive()):
                    worker.join()
                return toy.__exit__(*args)

        class FreshBleakAdapter:
            """Keep the BLEDevice returned by the scan for the connection.

            spherov2 0.12.1 normally discards that object and later gives only
            its address to BleakClient.  On Windows this triggers another
            implicit discovery and can raise BleakDeviceNotFoundError even for
            a device found moments earlier.
            """

            _devices: Dict[str, object] = {}
            _devices_lock = threading.Lock()
            _last_scan_report = "not scanned"

            @staticmethod
            def _key(value) -> str:
                return str(value or "").strip().upper()

            @classmethod
            def _remember(cls, device):
                if device is None:
                    return None
                with cls._devices_lock:
                    address = cls._key(getattr(device, "address", None))
                    name = cls._key(getattr(device, "name", None))
                    if address:
                        cls._devices[address] = device
                    if name:
                        cls._devices[name] = device
                return device

            @classmethod
            def scan_toys(cls, timeout: float = 5.0):
                discovered = asyncio.run(bleak.BleakScanner.discover(
                    timeout=timeout, return_adv=True))
                devices = []
                report = []
                for device, advertisement in discovered.values():
                    os_name = str(getattr(device, "name", None) or "")
                    local_name = str(getattr(advertisement, "local_name", None) or "")
                    # spherov2 0.12.1 filters only BLEDevice.name and otherwise
                    # silently ignores the device. Current Bleak keeps the
                    # advertised Sphero name in AdvertisementData.local_name.
                    if local_name and local_name != os_name:
                        try:
                            device.name = local_name
                        except Exception:
                            pass
                    cls._remember(device)
                    devices.append(device)
                    shown_name = str(getattr(device, "name", None) or local_name or "NO-NAME")
                    address = str(getattr(device, "address", None) or "NO-ADDRESS")
                    rssi = getattr(advertisement, "rssi", None)
                    report.append(f"{shown_name} ({address}, rssi={rssi if rssi is not None else 'n/a'})")
                cls._last_scan_report = (
                    f"devices={len(devices)} / " + (", ".join(report) if report else "none"))
                return devices

            @classmethod
            def scan_toy(cls, name: str, timeout: float = 5.0):
                device = asyncio.run(bleak.BleakScanner.find_device_by_filter(
                    lambda candidate, advertisement: (
                        getattr(advertisement, "local_name", None) == name or
                        getattr(candidate, "name", None) == name),
                    timeout=timeout))
                if device is not None and not getattr(device, "name", None):
                    try:
                        device.name = name
                    except Exception:
                        pass
                cls._last_scan_report = (
                    f"exact-name={name} / "
                    f"device={getattr(device, 'address', None) if device is not None else 'none'}")
                return cls._remember(device)

            @classmethod
            def scan_report(cls) -> str:
                return cls._last_scan_report

            def __init__(self, address):
                self.__event_loop = asyncio.new_event_loop()
                with self._devices_lock:
                    scanned_device = self._devices.get(self._key(address))
                # A BLEDevice carries WinRT's native Bluetooth address. Passing
                # it avoids Bleak's second advertisement scan, which fails when
                # a remembered Star Wars droid is asleep but still cached by
                # Windows. This is required for direct wake/reconnect.
                connection_target = scanned_device if scanned_device is not None else address
                client_options = {"timeout": 10.0}
                if sys.platform == "win32" and scanned_device is not None:
                    client_options["winrt"] = {
                        "address_type": "random", "use_cached_services": True}
                self.__device = bleak.BleakClient(connection_target, **client_options)
                self.__lock = threading.Lock()
                self.__thread = threading.Thread(
                    target=self.__event_loop.run_forever,
                    name="ControlDeck-BLE",
                    daemon=True)
                self.__thread.start()
                try:
                    self.__execute(self.__device.connect())
                except Exception:
                    self.close(False)
                    raise

            def __execute(self, coroutine):
                with self.__lock:
                    future = asyncio.run_coroutine_threadsafe(
                        coroutine, self.__event_loop)
                    return future.result()

            def close(self, disconnect=True):
                try:
                    if disconnect:
                        self.__execute(self.__device.disconnect())
                finally:
                    with self.__lock:
                        self.__event_loop.call_soon_threadsafe(
                            self.__event_loop.stop)
                    self.__thread.join(timeout=5.0)
                    if not self.__thread.is_alive():
                        self.__event_loop.close()

            def set_callback(self, uuid, callback):
                self.__execute(self.__device.start_notify(uuid, callback))

            def write(self, uuid, data):
                self.__execute(self.__device.write_gatt_char(uuid, data, True))

        self._scanner = scanner
        self._ble_device_class = BLEDevice
        self._adapter_class = FreshBleakAdapter
        self._animatronic_command = Animatronic
        self._to_bytes = to_bytes
        self._api_class = AwakeDisconnectSpheroEduAPI
        self._color_class = Color
        self._toy_util = ToyUtil
        self._types = {"r2d2": R2D2, "r2q5": R2Q5, "bb8e": BB8, "bb9e": BB9E}
        self._loaded = True

    def restore_known_devices(self, saved) -> None:
        """Load address/name pairs saved after earlier successful handshakes."""
        if not isinstance(saved, dict):
            return
        restored = {}
        for robot_id, raw in saved.items():
            if robot_id not in ROBOT_SPEC_BY_ID or not isinstance(raw, dict):
                continue
            address = str(raw.get("address", "")).strip()
            name = str(raw.get("name", "")).strip()
            if address and name and address not in ("—", "SIMULATED"):
                restored[robot_id] = {"address": address, "name": name}
        self._known_devices = restored

    def known_devices(self) -> Dict[str, Dict[str, str]]:
        result = {key: dict(value) for key, value in self._known_devices.items()}
        for robot_id, toy in self._toys.items():
            address = str(getattr(toy, "address", "") or "").strip()
            name = str(getattr(toy, "name", "") or "").strip()
            if address and name:
                result[robot_id] = {"address": address, "name": name}
        return result

    def _seed_known_toys(self) -> List[str]:
        """Create wrappers that can connect through cached WinRT GATT state."""
        self._load_library()
        seeded = []
        for robot_id, info in self._known_devices.items():
            if robot_id in self._toys:
                seeded.append(robot_id)
                continue
            address, name = info["address"], info["name"]
            details = None
            if sys.platform == "win32":
                try:
                    native_address = int(address.replace(":", "").replace("-", ""), 16)
                    details = SimpleNamespace(
                        adv=SimpleNamespace(bluetooth_address=native_address), scan=None)
                except ValueError:
                    continue
            device = self._ble_device_class(address, name, details, -127)
            self._adapter_class._remember(device)
            model_id = model_id_for(robot_id)
            self._toys[robot_id] = self._types[model_id](device, self._adapter_class)
            seeded.append(robot_id)
        return seeded

    @staticmethod
    def _device_key(device) -> str:
        return (getattr(device, "address", None) or
                getattr(device, "name", None) or repr(device))

    @staticmethod
    def _device_not_found(exc: Exception) -> bool:
        return (exc.__class__.__name__ == "BleakDeviceNotFoundError" or
                "was not found" in str(exc).lower())

    def _model_for_toy(self, toy) -> Optional[str]:
        """Return the most specific Droids Control for Windows model for a scanned toy."""
        # R2Q5 inherits from R2D2, and R2D2 inherits from BB9E.  This order is
        # therefore also required when the spherov2 scanner creates wrappers.
        for model_id in ("r2q5", "r2d2", "bb9e", "bb8e"):
            if isinstance(toy, self._types[model_id]):
                return model_id
        return None

    @staticmethod
    def _scan_toy_label(toy) -> str:
        name = str(getattr(toy, "name", None) or "NO-NAME")
        address = str(getattr(toy, "address", None) or "NO-ADDRESS")
        return f"{name} ({address})"

    def _scan_model_candidates(self, robot_id: str, model_id: str,
                               preferred=None) -> List[object]:
        """Scan one model, then fall back to an ordered all-droid scan.

        A targeted spherov2 scan can occasionally return no BB-8 on Windows
        even though the same advertisement is visible during a general scan.
        The second pass deliberately uses all supported classes in inheritance-
        safe order, then filters the result back to the requested model.
        """
        target_type = self._types[model_id]
        ordered_types = [self._types[key]
                         for key in ("r2q5", "r2d2", "bb9e", "bb8e")]
        preferred_name = (getattr(preferred, "name", None)
                          if preferred is not None else None)
        plans = []
        if preferred_name:
            plans.append(("exact-name", 8.0, [target_type], [preferred_name]))
        plans.extend((
            ("model-filter", 10.0, [target_type], None),
            ("all-supported", 10.0, ordered_types, None),
        ))

        report = []
        for label, timeout, toy_types, toy_names in plans:
            if self._stopped:
                break
            try:
                with self._scan_lock:
                    kwargs = {"timeout": timeout, "toy_types": toy_types,
                              "adapter": self._adapter_class}
                    if toy_names is not None:
                        kwargs["toy_names"] = toy_names
                    scanned = self._scanner.find_toys(**kwargs)
            except Exception as exc:
                report.append(f"{label}=ERROR {type(exc).__name__}: {exc or '<empty>'}")
                continue

            labels = ", ".join(self._scan_toy_label(toy) for toy in scanned)
            report.append(f"{label}={len(scanned)} [{labels or 'none'}]")
            matches = [toy for toy in scanned
                       if self._model_for_toy(toy) == model_id]
            if matches:
                self._scan_reports[robot_id] = "; ".join(report)
                return matches

        self._scan_reports[robot_id] = "; ".join(report) or "scan cancelled"
        return []

    def _rescan_instance(self, robot_id: str, model_id: str, preferred=None):
        """Refresh a stale BLE object without taking another slot's device."""
        toys = self._scan_model_candidates(robot_id, model_id, preferred)

        used_keys = {
            self._device_key(toy)
            for saved_id, toy in self._toys.items()
            if saved_id != robot_id
        }
        available = [toy for toy in toys if self._device_key(toy) not in used_keys]
        if not available:
            used = ", ".join(sorted(str(key) for key in used_keys)) or "none"
            self._scan_reports[robot_id] = (
                f"{self._scan_reports.get(robot_id, 'no scan report')}; "
                f"unused_matches=0; assigned_devices=[{used}]")
            return None

        chosen = None
        if preferred is not None:
            preferred_address = getattr(preferred, "address", None)
            preferred_name = getattr(preferred, "name", None)
            if preferred_address:
                chosen = next((toy for toy in available
                               if getattr(toy, "address", None) == preferred_address), None)
            if chosen is None and preferred_name:
                chosen = next((toy for toy in available
                               if getattr(toy, "name", None) == preferred_name), None)
            # A Windows BLE refresh may change the identifier. Falling back is
            # safe only when exactly one unassigned device of this model exists.
            if chosen is None and len(available) == 1:
                chosen = available[0]
        else:
            chosen = available[0]

        if chosen is not None:
            self._toys[robot_id] = chosen
        return chosen

    def discover(self) -> List[str]:
        self._load_library()
        if self._stopped or self._power_paused:
            return []
        # Include remembered slots before scanning. Their later connect() call
        # first uses Windows' cached native device handle and sends wake during
        # SpheroEduAPI initialization; active scanning remains the fallback.
        found: List[str] = self._seed_known_toys()
        # Keep the scanner wrapper selection inheritance-safe as well as the
        # classification loop below.  A dict insertion order is not sufficient
        # because R2Q5 is a subclass of R2D2.
        toy_types = [self._types[key]
                     for key in ("r2q5", "r2d2", "bb9e", "bb8e")]
        with self._scan_lock:
            toys = self._scanner.find_toys(
                timeout=10.0, toy_types=toy_types,
                adapter=self._adapter_class)
        self._scan_reports["discovery"] = self._adapter_class.scan_report()
        used_addresses = {self._device_key(toy) for toy in self._toys.values()}
        for toy in toys:
            model_id = self._model_for_toy(toy)
            if model_id is None:
                continue
            address = self._device_key(toy)
            if address in used_addresses:
                existing = next((rid for rid, saved in self._toys.items()
                                 if self._device_key(saved) == address), None)
                if existing and existing not in found:
                    found.append(existing)
                continue
            free_slot = next((instance_id(model_id, slot) for slot in (1, 2)
                              if instance_id(model_id, slot) not in self._toys), None)
            if free_slot:
                self._toys[free_slot] = toy
                used_addresses.add(address)
                found.append(free_slot)
        return found

    def scan_diagnostics(self) -> str:
        return self._scan_reports.get("discovery", "")

    def connect(self, robot_id: str) -> bool:
        self._load_library()
        model_id = model_id_for(robot_id)
        with self._lock:
            if (self._stopped or self._power_paused or model_id not in self._types
                    or robot_id not in self._robot_locks):
                return False
            if robot_id not in self._toys:
                # Manual tile click: rescan this model and take an unused device.
                if self._rescan_instance(robot_id, model_id) is None:
                    toy_type = self._types[model_id].toy_type
                    expected = (getattr(toy_type, "prefix", None) or
                                getattr(toy_type, "filter_prefix", None) or model_id)
                    raise RuntimeError(
                        f"No matching unassigned device for slot {robot_id}; "
                        f"model={model_id}, expected_name_prefix={expected}, "
                        f"scan_report={self._scan_reports.get(robot_id, 'unavailable')}")
            if robot_id in self._apis:
                return True

            original_error = None
            for attempt in range(2):
                toy = self._toys[robot_id]
                api = self._api_class(toy)
                try:
                    api.__enter__()
                    if model_id == "bb9e":
                        # SpheroEduAPI initializes its local stabilization flag
                        # to True, but set_robot_state_on_start() does not send
                        # the FULL_CONTROL_SYSTEM command. BB-9E may therefore
                        # acknowledge heading updates without moving. Require
                        # the drive controller acknowledgement as part of the
                        # connection handshake.
                        api.set_stabilization(True)
                        time.sleep(0.12)
                    self._apis[robot_id] = api
                    return True
                except Exception as exc:
                    try:
                        api.__exit__(None, None, None)
                    except Exception:
                        pass
                    if attempt == 0 and self._device_not_found(exc):
                        original_error = exc
                        refreshed = self._rescan_instance(robot_id, model_id, preferred=toy)
                        if refreshed is not None:
                            continue
                        raise RuntimeError(
                            f"BLE device disappeared and a fresh {model_id.upper()} scan "
                            f"found no matching unassigned device for slot {robot_id}; "
                            f"previous={self._device_key(toy)}"
                        ) from exc
                    if original_error is not None:
                        raise RuntimeError(
                            f"Connection retry failed after a fresh {model_id.upper()} scan; "
                            f"slot={robot_id}, previous_error={type(original_error).__name__}: "
                            f"{original_error}, refreshed={self._device_key(toy)}, "
                            f"retry_error={type(exc).__name__}: {exc}"
                        ) from exc
                    raise
            return False

    def _api(self, robot_id: str):
        if self._power_paused:
            raise RuntimeError("Bluetooth operations paused for Windows power transition")
        if self._stopped or robot_id not in self._apis:
            raise RuntimeError("Robot is not connected")
        return self._apis[robot_id]

    def _is_astromech(self, robot_id: str) -> bool:
        return model_id_for(robot_id) in ("r2d2", "r2q5")

    def get_animation_catalog(self) -> Dict[str, List[str]]:
        self._load_library()
        return {
            "r2d2": list(R2D2_FIRMWARE_ANIMATIONS),
            "r2q5": list(R2Q5_FIRMWARE_ANIMATIONS),
            "bb8e": list(BB8_MOTION_PATHS),
            "bb9e": list(BB9E_FIRMWARE_ANIMATIONS),
        }

    def identity(self, robot_id: str) -> str:
        toy = self._toys.get(robot_id)
        return repr(toy) if toy is not None else "UNASSIGNED BLE DEVICE"

    def connection_id(self, robot_id: str) -> str:
        toy = self._toys.get(robot_id)
        if toy is None:
            return "NO DEVICE"
        return (getattr(toy, "name", None) or
                getattr(toy, "address", None) or self._device_key(toy))

    def connection_details(self, robot_id: str) -> Dict[str, str]:
        toy = self._toys.get(robot_id)
        if toy is None:
            return {"name": "NO DEVICE", "address": "—", "api_id": robot_id}
        return {
            "name": str(getattr(toy, "name", None) or self.connection_id(robot_id)),
            "address": str(getattr(toy, "address", None) or "—"),
            "api_id": robot_id,
        }

    def get_battery(self, robot_id: str) -> Dict[str, object]:
        """Read real battery voltage and derive a clearly marked estimate."""
        with self._robot_locks[robot_id]:
            self._api(robot_id)  # Validate the live session.
            toy = self._toys[robot_id]
            voltage_getter = getattr(toy, "get_battery_voltage", None)
            power_getter = getattr(toy, "get_power_state", None)
            power_state = None
            source = ""
            if callable(voltage_getter):
                raw = voltage_getter()
                source = "toy.get_battery_voltage"
                state_getter = getattr(toy, "get_battery_state", None)
                if callable(state_getter):
                    try:
                        state_value = state_getter()
                        power_state = getattr(state_value, "name", str(state_value))
                    except Exception:
                        # Voltage remains useful even if the optional state call
                        # is unsupported by a particular firmware revision.
                        power_state = None
            elif callable(power_getter):
                raw_state = power_getter()
                raw = getattr(raw_state, "voltage", None)
                state_value = getattr(raw_state, "state", None)
                power_state = (getattr(state_value, "name", str(state_value))
                               if state_value is not None else None)
                source = "toy.get_power_state"
            else:
                raise RuntimeError(
                    f"Toy class {type(toy).__name__} exports neither "
                    "get_battery_voltage() nor get_power_state()")
            try:
                voltage = float(getattr(raw, "value", raw))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Invalid battery-voltage response: {raw!r}") from exc
            # Accommodate firmware/library variants returning V, dV, cV or mV.
            # BB-9E firmware has been observed returning 0.77 through the
            # library's already-scaled getter, representing a 7.7 V pack.
            while 0.0 < voltage < 2.5:
                voltage *= 10.0
            while voltage > 10.0:
                voltage /= 10.0
            if not 2.5 <= voltage <= 8.8:
                raise RuntimeError(f"Battery voltage outside expected range: raw={raw!r}, normalized={voltage}")
            cell_voltage = voltage / 2.0 if voltage > 5.5 else voltage
            percent = round(max(0.0, min(100.0, (cell_voltage - 3.25) / 0.90 * 100.0)))
            return {"percent": percent, "voltage": voltage,
                    "power_state": power_state, "source": source}

    def move_head(self, robot_id: str) -> bool:
        with self._robot_locks[robot_id]:
            api = self._api(robot_id)
            if self._is_astromech(robot_id):
                angle = random.choice((-28, -18, 18, 28))
                api.set_dome_position(angle)
                time.sleep(0.35)
                api.set_dome_position(0)
            else:
                # BB-8/BB-9E have no separate head motor command.
                # A minimal body turn provides safe physical confirmation.
                api.spin(random.choice((-18, 18)), 0.35)
            return True

    def play_connection_confirmation(self, robot_id: str) -> str:
        """Run one randomly selected, model-specific connection animation.

        Native sleep animations are intentionally excluded: a confirmation
        must prove that the session is responsive without making the droid
        disappear from subsequent BLE scans. BB-8 uses a short safe path from
        the original application's drive-animation catalog.
        """
        with self._robot_locks[robot_id]:
            self._api(robot_id)
            model_id = model_id_for(robot_id)
            if model_id == "bb8e":
                chosen = random.choice((
                    "BB8_PATH_YES", "BB8_PATH_NO", "BB8_PATH_SHORT_SHAKE",
                    "BB8_PATH_HEADTURN45LEFT", "BB8_PATH_HEADTURN45RIGHT",
                ))
            else:
                chosen = random.choice([
                    name for name in FIRMWARE_ANIMATIONS[model_id]
                    if "SLEEP" not in name and name != "MOTOR"
                ])
            self.execute_animation(robot_id, chosen)
            return (f"BB-8 drive path {chosen}" if model_id == "bb8e"
                    else f"native animation {chosen}")

    def _execute_bb8_path(self, robot_id: str, animation_name: str) -> bool:
        """Play one original-app BB-8 keyframe path through drive packets."""
        path = BB8_MOTION_PATHS.get(animation_name)
        if path is None:
            raise ValueError(f"{animation_name} is not an available BB-8 drive path")
        frames = path.get("frames") or []
        if not frames:
            raise ValueError(f"{animation_name} contains no BB-8 keyframes")
        toy = self._toys[robot_id]
        first_frame = int(frames[0][0])
        last_frame = max(first_frame + 1, int(frames[-1][0]))
        duration = max(0.25, float(path.get("time") or (last_frame - first_frame) / 24.0))
        frame_span = max(1, last_frame - first_frame)
        try:
            for index, (frame, heading, speed) in enumerate(frames):
                if self._stopped or self._power_paused:
                    raise RuntimeError("BB-8 path interrupted by shutdown or power transition")
                bounded_speed = max(-255, min(255, int(round(float(speed)))))
                self._toy_util.roll_start(toy, int(round(float(heading))) % 360,
                                          bounded_speed)
                if index + 1 < len(frames):
                    next_frame = int(frames[index + 1][0])
                    wait = max(0.0, (next_frame - int(frame)) / frame_span * duration)
                    if wait:
                        time.sleep(wait)
            return True
        finally:
            try:
                self._toy_util.roll_stop(toy, int(round(float(frames[-1][1]))) % 360,
                                         False)
            except Exception:
                pass

    def execute_animation(self, robot_id: str, animation_name: str) -> bool:
        with self._robot_locks[robot_id]:
            self._api(robot_id)  # Validate the live session.
            model_id = model_id_for(robot_id)
            animation_name = ANIMATION_ALIASES.get(model_id, {}).get(
                animation_name, animation_name)
            if model_id == "bb8e":
                return self._execute_bb8_path(robot_id, animation_name)
            toy = self._toys[robot_id]
            animation_id = FIRMWARE_ANIMATIONS.get(model_id, {}).get(animation_name)
            if animation_id is None:
                raise ValueError(f"{animation_name} does not exist in this robot's firmware catalog")
            # spherov2 0.12.1 has a parameter mix-up in ToyUtil.play_animation:
            # its `wait=True` value is forwarded as Animatronic's `proc=True`,
            # producing target 0x11. R2/BB9E are non-target ToyV2 devices and the
            # working head command uses proc=None. Build the animation identically.
            # Still wait for the firmware response and validate its error field.
            packet = self._animatronic_command._encode(
                toy, 5, None, self._to_bytes(animation_id, 2))
            try:
                response = toy._execute(packet)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"No firmware acknowledgement within 10 s; animation={animation_name}, "
                    f"firmware_id={animation_id}, did=23, cid=5, target=none, "
                    f"toy={toy!r}. The BLE session may be stale or the animatronic "
                    "processor may be unavailable."
                ) from exc
            response.check_error()
            return True

    def execute_standard(self, robot_id: str, command_id: str) -> bool:
        with self._robot_locks[robot_id]:
            api = self._api(robot_id)
            head_angles = {
                "head_left_small": -30, "head_left_large": -120,
                "head_right_small": 30, "head_right_large": 120,
            }
            turn_angles = {
                "turn_left_small": (-45, 0.45), "turn_left_large": (-180, 0.9),
                "turn_right_small": (45, 0.45), "turn_right_large": (180, 0.9),
            }
            if command_id in head_angles:
                if not self._is_astromech(robot_id):
                    raise ValueError("This robot does not have a controllable dome")
                api.set_dome_position(head_angles[command_id])
            elif command_id in turn_angles:
                angle, duration = turn_angles[command_id]
                api.spin(angle, duration)
            else:
                raise ValueError(f"Unknown standard command: {command_id}")
            return True

    def set_led(self, robot_id: str, channel: str, enabled: bool) -> bool:
        with self._robot_locks[robot_id]:
            api = self._api(robot_id)
            model_id = model_id_for(robot_id)
            accent = ROBOT_SPEC_BY_ID[robot_id].accent
            rgb = tuple(int(accent[index:index + 2], 16) for index in (1, 3, 5)) if enabled else (0, 0, 0)
            color = self._color_class(*rgb)
            if model_id in ("r2d2", "r2q5"):
                if channel == "front":
                    api.set_front_led(color)
                elif channel == "back":
                    api.set_back_led(color)
                elif channel == "logic":
                    api.set_logic_display_leds(255 if enabled else 0)
                elif channel == "holo":
                    api.set_holo_projector_led(255 if enabled else 0)
                else:
                    raise ValueError(f"Unknown LED channel: {channel}")
            elif model_id == "bb9e":
                if channel == "body":
                    api.set_main_led(color)
                elif channel == "aiming":
                    api.set_back_led(255 if enabled else 0)
                elif channel == "dome":
                    api.set_dome_leds(15 if enabled else 0)
                else:
                    raise ValueError(f"Unknown LED channel: {channel}")
            else:
                raise ValueError("This model has no independent LED channels")
            return True

    def get_orientation(self, robot_id: str) -> Dict[str, Optional[float]]:
        """Read cached attitude sensors without blocking Tk's UI thread."""
        with self._robot_locks[robot_id]:
            api = self._api(robot_id)
            attitude = api.get_orientation() or {}
            heading = api.get_heading()

            def number(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            yaw = number(attitude.get("yaw"))
            heading_value = number(heading)
            # Yaw is the measured sensor direction; heading is the API's current
            # target direction and remains useful when attitude streaming is absent.
            direction = yaw % 360.0 if yaw is not None else heading_value
            if direction is not None:
                direction %= 360.0
            return {
                "direction": direction,
                "heading": heading_value,
                "yaw": yaw,
                "pitch": number(attitude.get("pitch")),
                "roll": number(attitude.get("roll")),
            }

    def stop_all(self, robot_id: str) -> None:
        with self._robot_locks[robot_id]:
            api = self._apis.get(robot_id)
            if not api:
                return
            try:
                api.stop_roll()
            except Exception:
                pass
            if self._is_astromech(robot_id):
                try:
                    api.set_waddle(False)
                    api.set_dome_position(0)
                except Exception:
                    pass
            for channel, _label in LED_CHANNELS.get(model_id_for(robot_id), ()):
                try:
                    self.set_led(robot_id, channel, False)
                except Exception:
                    pass

    def disconnect(self, robot_id: str) -> None:
        with self._lock:
            api = self._apis.get(robot_id)
            if not api:
                return
            try:
                self.stop_all(robot_id)
            finally:
                try:
                    api.__exit__(None, None, None)
                finally:
                    self._apis.pop(robot_id, None)

    def forget(self, robot_id: str) -> None:
        with self._lock:
            self._apis.pop(robot_id, None)
            self._toys.pop(robot_id, None)

    def reset_connection(self, robot_id: str) -> None:
        """Drop a stale BLE session without sending more commands to it."""
        with self._robot_locks[robot_id]:
            with self._lock:
                api = self._apis.pop(robot_id, None)
                self._toys.pop(robot_id, None)
            if api is not None:
                api.__exit__(None, None, None)

    def pause_for_system_sleep(self) -> None:
        """Reject newly queued commands while Windows suspends the process."""
        self._power_paused = True

    def resume_after_system_sleep(self) -> None:
        self._power_paused = False

    def finalize_shutdown(self) -> None:
        """Reject new work after per-robot shutdown workers have started."""
        self._stopped = True

    def shutdown(self) -> None:
        # Stop first, then disconnect every active device.
        for robot_id in list(self._apis):
            try:
                self.stop_all(robot_id)
            except Exception:
                pass
        for robot_id in list(self._apis):
            try:
                self.disconnect(robot_id)
            except Exception:
                pass
        self._stopped = True


class NeonButton(tk.Canvas):
    def __init__(self, master, text: str, command: Callable[[], None], width=110, height=34, active=False):
        super().__init__(master, width=width, height=height, bg=PANEL, highlightthickness=0, cursor="hand2")
        self.command = command
        self.label = text
        self.active = active
        self.bind("<Button-1>", lambda _e: self.command())
        self.bind("<Enter>", lambda _e: self.draw(True))
        self.bind("<Leave>", lambda _e: self.draw(False))
        self.draw(False)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.draw(False)

    def draw(self, hover: bool) -> None:
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        color = CYAN if self.active or hover else LINE_BRIGHT
        fill = "#0D2733" if self.active or hover else PANEL
        self.create_polygon(1, 1, w - 8, 1, w - 1, 8, w - 1, h - 1, 8, h - 1, 1, h - 8,
                            fill=fill, outline=color, width=1)
        self.create_text(w / 2, h / 2, text=self.label, fill=CYAN if self.active else TEXT,
                         font=("TkDefaultFont", 8, "bold"))


class LedToggle(tk.Canvas):
    def __init__(self, master, text: str, accent: str, command: Callable[[], None],
                 width=116, danger: bool = False):
        super().__init__(master, width=width, height=34, bg=PANEL_2, highlightthickness=0, cursor="hand2")
        self.label = text
        self.accent = accent
        self.command = command
        self.danger = danger
        self.active = False
        self.enabled = False
        self.hover = False
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.draw()

    def _click(self, _event) -> None:
        if self.enabled:
            self.command()

    def _hover(self, value: bool) -> None:
        self.hover = value
        self.draw()

    def update_state(self, active: bool, enabled: bool) -> None:
        self.active = active
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w, h = int(self["width"]), 34
        if self.danger:
            color = self.accent if self.enabled else "#69283A"
            fill = "#3A111D" if self.enabled and self.hover else "#241019"
        else:
            color = self.accent if self.active or (self.enabled and self.hover) else (LINE_BRIGHT if self.enabled else LINE)
            fill = "#102A35" if self.active else PANEL_2
        self.create_polygon(1, 1, w - 7, 1, w - 1, 7, w - 1, h - 1, 7, h - 1, 1, h - 7,
                            fill=fill, outline=color, width=2 if self.active else 1)
        self.create_oval(9, 13, 17, 21, fill=color if self.active else PANEL_2, outline=color)
        text_color = self.accent if self.danger and self.enabled else (TEXT if self.enabled else MUTED)
        self.create_text(23, h / 2, text=self.label, anchor="w",
                         fill=text_color, font=("TkDefaultFont", 6, "bold"))


class RobotTile(tk.Canvas):
    def __init__(self, master, state: RobotState, click_callback: Callable[[str, bool], None]):
        super().__init__(master, width=220, height=176, bg=PANEL, highlightthickness=0, cursor="hand2")
        self.state = state
        self.click_callback = click_callback
        self.phase = random.random() * math.tau
        self.hover = False
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.draw()

    def _click(self, event) -> None:
        # Only the small top-right checkbox changes group membership.
        toggle_group = event.x >= 184 and event.y <= 34
        self.click_callback(self.state.spec.robot_id, toggle_group)

    def _enter(self, _event) -> None:
        self.hover = True
        self.draw()

    def _leave(self, _event) -> None:
        self.hover = False
        self.draw()

    def tick(self) -> None:
        self.phase = (self.phase + 0.045) % math.tau
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w, h = 220, 176
        accent = self.state.spec.accent
        selected = self.state.selected and self.state.connected
        focused = self.state.focused
        active = focused
        outline = accent if focused or selected or self.hover else LINE
        fill = "#0B1B26" if focused else ("#0A1822" if selected else "#08121B")

        # Multi-layer HUD panel with clipped corners.
        self.create_polygon(1, 12, 12, 1, w - 26, 1, w - 1, 26, w - 1, h - 12,
                            w - 12, h - 1, 24, h - 1, 1, h - 24,
                            fill=fill, outline=outline, width=2 if active else 1)
        self.create_line(13, 7, w - 31, 7, fill="#12384A")
        self.create_line(7, 19, 7, h - 29, fill="#12384A")
        self.create_line(w - 7, 31, w - 7, h - 17, fill="#12384A")
        self.create_line(29, h - 7, w - 17, h - 7, fill="#12384A")
        for x in range(18, w - 35, 14):
            self.create_line(x, 11, x + 7, 11, fill=LINE_BRIGHT)
        if active:
            travel = int((math.sin(self.phase) + 1) * 58)
            self.create_line(18 + travel, 2, 60 + travel, 2, fill=TEXT, width=2)
            self.create_line(w - 1, 38 + travel // 2, w - 1, 71 + travel // 2, fill=accent, width=3)
            self.create_arc(49, 17, 171, 139, start=(self.phase * 52) % 360, extent=48,
                            style="arc", outline=accent, width=2)

        # Reticle, grid, and side micro-telemetry.
        self.create_oval(51, 17, 169, 135, outline="#12384A")
        self.create_oval(62, 28, 158, 124, outline="#102C3B")
        self.create_line(110, 18, 110, 128, fill="#102B39")
        self.create_line(54, 76, 166, 76, fill="#102B39")
        for y, width in ((31, 21), (39, 14), (47, 25), (55, 11)):
            self.create_line(14, y, 14 + width, y, fill=LINE_BRIGHT)
        for y in range(30, 79, 8):
            self.create_rectangle(186, y, 190 + int((math.sin(self.phase + y) + 1) * 8), y + 3,
                                  fill=accent if y % 16 == 0 else LINE_BRIGHT, outline="")

        self._draw_robot(w / 2, 72, accent)

        # Checkbox / selection indicator.
        x = w - 25
        self.create_polygon(x, 10, x + 10, 10, x + 14, 14, x + 14, 24, x + 4, 24, x, 20,
                            outline=accent if selected else LINE_BRIGHT,
                              fill="#12303B" if selected else PANEL)
        if selected:
            self.create_line(x + 3, 17, x + 6, 21, x + 12, 13, fill=accent, width=2)

        status_color = GREEN if self.state.connected else MUTED
        status = ("CONNECTED" if self.state.connected else
                  ("CONNECTING" if self.state.connecting else
                   ("DETECTED" if self.state.discovered else "CLICK TO CONNECT")))
        if self.state.connection_stale:
            status = ("RECONNECTING" if self.state.connecting else
                      "STALE / CLICK TO RECONNECT")
            status_color = RED
        elif self.state.connecting:
            status_color = AMBER
        self.create_rectangle(15, 130, w - 15, 158, fill="#091721", outline=LINE)
        self.create_text(21, 140, text=self.state.spec.name, anchor="w", fill=TEXT,
                         font=("TkDefaultFont", 11, "bold"))
        self.create_text(w - 20, 140, text=f"ID:{self.state.spec.robot_id.upper()}", anchor="e", fill=accent,
                         font=("Courier", 6, "bold"))
        self.create_oval(21, 148, 27, 154, fill=status_color, outline="")
        self.create_text(33, 151, text=status, anchor="w", fill=status_color,
                         font=("TkDefaultFont", 7, "bold"))
        self.create_text(w - 20, 151, text=self.state.spec.robot_type, anchor="e", fill=MUTED,
                         font=("TkDefaultFont", 6))
        self.create_text(15, 166, text="SYS/BT  2.4GHz", anchor="w", fill="#34586B", font=("Courier", 5))
        self.create_text(w - 15, 166, text="RX ▮▮▮▯", anchor="e", fill="#34586B", font=("Courier", 5))

    def _draw_robot(self, cx: float, cy: float, accent: str) -> None:
        offset = math.sin(self.phase) * 2.5
        scan_angle = (self.phase * 55) % 360
        if self.state.spec.shape == "astro":
            x = cx + offset
            is_r2d6 = self.state.spec.model_id == "r2d2" and self.state.spec.slot == 2
            shell = ("#151D27" if is_r2d6 else
                     ("#122B39" if self.state.spec.model_id == "r2d2" else "#21141D"))
            # Segmented dome with projector and optical panels.
            if is_r2d6:
                # R2-D6: a slightly taller, faceted dome with a flattened crown.
                self.create_polygon(x - 31, cy - 18, x - 27, cy - 34,
                                    x - 16, cy - 44, x - 9, cy - 48,
                                    x + 12, cy - 48, x + 23, cy - 39,
                                    x + 29, cy - 29, x + 31, cy - 18,
                                    fill=shell, outline=accent, width=2)
                self.create_line(x - 9, cy - 44, x + 12, cy - 44,
                                 fill="#8BDCF0", width=2)
                self.create_line(x - 24, cy - 33, x + 25, cy - 33,
                                 fill=LINE_BRIGHT)
            else:
                self.create_arc(x - 31, cy - 46, x + 31, cy + 10, start=0, extent=180,
                                style="pieslice", fill=shell, outline=accent, width=2)
                self.create_arc(x - 27, cy - 42, x + 27, cy + 6, start=18, extent=144,
                                style="arc", outline="#8BDCF0")
            for dx in (-18, -8, 3, 14):
                self.create_line(x + dx, cy - 42 + abs(dx) / 7, x + dx - 3, cy - 21,
                                 fill=LINE_BRIGHT)
            self.create_rectangle(x - 18, cy - 25, x + 4, cy - 17, fill="#07121A", outline=accent)
            self.create_line(x - 15, cy - 21, x + 1, cy - 21, fill="#B8F5FF")
            self.create_oval(x + 10, cy - 34, x + 20, cy - 24, fill=accent, outline="#C2F8FF")
            self.create_oval(x + 13, cy - 31, x + 17, cy - 27, fill="#F04D7D", outline="")
            # Body with multiple panels and wiring.
            self.create_polygon(x - 31, cy - 18, x + 31, cy - 18, x + 27, cy + 35,
                                x + 18, cy + 42, x - 18, cy + 42, x - 27, cy + 35,
                                fill="#0B1B25", outline=accent, width=2)
            self.create_rectangle(x - 20, cy - 12, x + 20, cy - 2, outline=accent)
            for px in (-16, -8, 0, 8):
                self.create_rectangle(x + px, cy - 9, x + px + 5, cy - 5,
                                      fill=accent if px in (-16, 8) else LINE_BRIGHT, outline="")
            self.create_rectangle(x - 20, cy + 4, x - 3, cy + 20, outline=LINE_BRIGHT)
            self.create_rectangle(x + 3, cy + 4, x + 20, cy + 20, outline=accent)
            self.create_line(x - 15, cy + 8, x - 7, cy + 16, x - 15, cy + 16, fill=accent)
            self.create_oval(x + 8, cy + 8, x + 15, cy + 15, outline="#AEEFFF")
            self.create_rectangle(x - 8, cy + 24, x + 8, cy + 36, outline=LINE_BRIGHT)
            self.create_line(x - 4, cy + 27, x - 4, cy + 34, fill=accent)
            self.create_line(x + 1, cy + 27, x + 1, cy + 34, fill=accent)
            self.create_line(x + 6, cy + 27, x + 6, cy + 34, fill=accent)
            # Technical legs and feet.
            self.create_polygon(x - 30, cy + 9, x - 39, cy + 35, x - 35, cy + 48,
                                x - 17, cy + 48, x - 19, cy + 38, x - 22, cy + 13,
                                fill="#0A1720", outline=accent)
            self.create_polygon(x + 30, cy + 9, x + 39, cy + 35, x + 35, cy + 48,
                                x + 17, cy + 48, x + 19, cy + 38, x + 22, cy + 13,
                                fill="#0A1720", outline=accent)
            self.create_line(x - 34, cy + 35, x - 20, cy + 35, fill=LINE_BRIGHT)
            self.create_line(x + 34, cy + 35, x + 20, cy + 35, fill=LINE_BRIGHT)
            # Rotating scanner around the silhouette.
            self.create_arc(x - 47, cy - 55, x + 47, cy + 54, start=scan_angle, extent=52,
                            style="arc", outline=accent, width=2)
            self.create_arc(x - 43, cy - 51, x + 43, cy + 50, start=scan_angle + 182, extent=24,
                            style="arc", outline="#6CCBE2")
        else:
            x = cx + offset
            shell = "#241D14" if self.state.spec.model_id == "bb8e" else "#111820"
            self.create_oval(x - 37, cy - 23, x + 37, cy + 51, fill=shell, outline=accent, width=2)
            # Spherical body segments.
            self.create_arc(x - 33, cy - 19, x + 33, cy + 47, start=20, extent=52,
                            style="arc", outline=LINE_BRIGHT, width=2)
            self.create_arc(x - 33, cy - 19, x + 33, cy + 47, start=110, extent=48,
                            style="arc", outline=accent, width=2)
            self.create_arc(x - 33, cy - 19, x + 33, cy + 47, start=200, extent=58,
                            style="arc", outline=LINE_BRIGHT, width=2)
            self.create_oval(x - 14, cy + 4, x + 14, cy + 32, outline=accent, width=2)
            self.create_oval(x - 7, cy + 11, x + 7, cy + 25, outline="#A5EBFA")
            for angle in range(0, 360, 45):
                r1, r2 = 15, 29
                a = math.radians(angle + scan_angle / 5)
                self.create_line(x + math.cos(a) * r1, cy + 18 + math.sin(a) * r1,
                                 x + math.cos(a) * r2, cy + 18 + math.sin(a) * r2,
                                 fill=LINE_BRIGHT)
            # Head with optical slots and antenna. BB-9E uses its characteristic
            # low, angular truncated dome instead of BB-8's round hemisphere.
            if self.state.spec.model_id == "bb9e":
                # Deliberately angular truncated-cone silhouette: broad lower
                # brim, straight facets and a narrow flat crown.
                self.create_polygon(x - 31, cy - 21, x - 31, cy - 28,
                                    x - 23, cy - 42, x - 11, cy - 49,
                                    x + 11, cy - 49, x + 23, cy - 42,
                                    x + 31, cy - 28, x + 31, cy - 21,
                                    fill="#07131C", outline=accent, width=3)
                self.create_polygon(x - 25, cy - 24, x - 20, cy - 39,
                                    x - 9, cy - 45, x - 4, cy - 24,
                                    fill="#0D2432", outline=LINE_BRIGHT)
                self.create_polygon(x + 4, cy - 24, x + 9, cy - 45,
                                    x + 20, cy - 39, x + 25, cy - 24,
                                    fill="#0D2432", outline=LINE_BRIGHT)
                self.create_rectangle(x - 11, cy - 50, x + 11, cy - 45,
                                      fill="#0B1D29", outline=accent, width=2)
                self.create_line(x - 30, cy - 22, x + 30, cy - 22,
                                 fill=accent, width=3)
            else:
                self.create_arc(x - 27, cy - 48, x + 27, cy + 6, start=0, extent=180,
                                style="pieslice", fill="#0C1821", outline=accent, width=2)
            self.create_line(x - 27, cy - 21, x + 27, cy - 21, fill=accent, width=2)
            self.create_rectangle(x - 15, cy - 36, x + 3, cy - 29, fill="#050A0F", outline=LINE_BRIGHT)
            self.create_oval(x + 9, cy - 39, x + 19, cy - 29, fill=accent, outline="#C6F7FF")
            self.create_oval(x + 12, cy - 36, x + 16, cy - 32, fill=RED, outline="")
            self.create_line(x - 9, cy - 47, x - 11, cy - 58, fill=accent, width=2)
            self.create_line(x + 2, cy - 47, x + 5, cy - 54, fill=LINE_BRIGHT)
            self.create_arc(x - 48, cy - 58, x + 48, cy + 57, start=scan_angle, extent=58,
                            style="arc", outline=accent, width=2)


class GestureButton(tk.Canvas):
    def __init__(self, master, gesture, command: Callable[[str], None]):
        super().__init__(master, height=120, bg=PANEL, highlightthickness=0, cursor="hand2")
        self.gesture_id, self.title, self.description, self.icon = gesture
        self.center_leg_safe = self.gesture_id in CENTER_LEG_SAFE_COMMANDS
        self.command = command
        self.supported_by: List[RobotState] = []
        self.enabled_for: List[RobotState] = []
        self.highlighted = False
        self.hover = False
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Configure>", lambda _e: self.draw())

    def _set_hover(self, value: bool) -> None:
        self.hover = value
        self.draw()

    def _click(self, _event) -> None:
        if self.enabled_for:
            self.command(self.gesture_id)

    def update_state(self, supported_by: List[RobotState], enabled_for: List[RobotState], highlighted: bool) -> None:
        self.supported_by = list(supported_by)
        self.enabled_for = list(enabled_for)
        self.highlighted = highlighted
        self.configure(cursor="hand2" if self.enabled_for else "arrow")
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w, h = max(self.winfo_width(), 180), 120
        enabled = bool(self.enabled_for)
        bright = enabled and (self.highlighted or self.hover)
        outline = CYAN if bright else (LINE_BRIGHT if enabled else "#152631")
        fill = "#0D2531" if bright else PANEL
        fg = CYAN if enabled else "#38505E"
        self.create_polygon(1, 1, w - 11, 1, w - 1, 11, w - 1, h - 1, 11, h - 1, 1, h - 11,
                            fill=fill, outline=outline, width=2 if bright else 1)
        self.create_line(11, 7, 72, 7, fill=fg)
        self.create_line(w - 54, h - 7, w - 11, h - 7, fill=fg)
        self.create_polygon(17, 17, 43, 17, 52, 26, 43, 52, 17, 52, 8, 43, 8, 26,
                            outline=fg, fill="#0A1822")
        self.create_oval(16, 25, 44, 53, outline=LINE_BRIGHT)
        self.create_text(30, 39, text=self.icon, fill=fg,
                         font=("TkDefaultFont", 15, "bold"))
        for y in (23, 31, 39, 47):
            self.create_line(61, y, 81 + (y % 3) * 7, y, fill=LINE_BRIGHT)
        self.create_text(w - 12, 31, text=f"CMD/{self.gesture_id.upper()}", anchor="e",
                         fill="#416477", font=("Courier", 5, "bold"))
        for i, robot in enumerate(reversed(self.supported_by)):
            x2 = w - 11 - i * 15
            self.create_rectangle(x2 - 10, 14, x2, 21, fill=robot.spec.accent,
                                  outline=TEXT if robot.connected else LINE)
        self.create_text(18, 66, anchor="w", text=self.title, fill=TEXT if enabled else MUTED,
                         font=("TkDefaultFont", 10, "bold"))
        self.create_text(18, 87, anchor="w", text=self.description.upper(), fill=MUTED,
                         font=("TkDefaultFont", 7))
        if self.center_leg_safe:
            badge_x1 = max(90, w - 112)
            self.create_polygon(
                badge_x1, 79, w - 17, 79, w - 10, 86, w - 17, 95, badge_x1, 95,
                fill="#0B2B25", outline=GREEN)
            self.create_text(w - 20, 87, anchor="e", text="CENTER LEG SAFE",
                             fill=GREEN, font=("Courier", 6, "bold"))
        for i, robot in enumerate(self.supported_by):
            segment = min(34, max(12, int((w - 36) / max(1, len(self.supported_by)))))
            x1 = 18 + i * segment
            self.create_rectangle(x1, 102, x1 + segment - 3, 106,
                                  fill=robot.spec.accent, outline="")


class MacroCard(tk.Canvas):
    """Futuristic scene tile used by the Macros Matrix."""

    def __init__(self, master, app, scene_name: Optional[str] = None):
        super().__init__(master, height=142, bg=PANEL, highlightthickness=0,
                         cursor="hand2")
        self.app = app
        self.scene_name = scene_name
        self.hover = False
        self.bind("<Configure>", lambda _e: self.draw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._click)

    def _set_hover(self, value: bool) -> None:
        self.hover = value
        self.draw()

    def _click(self, event) -> None:
        if self.scene_name is None:
            self.app._new_macro_scene()
            return
        width = max(self.winfo_width(), 240)
        if event.y >= 106 and event.x >= width - 92:
            self.app._open_macro_editor(self.scene_name)
        elif event.y >= 106:
            self.app._run_macro_scene(self.scene_name)
        else:
            self.app._open_macro_editor(self.scene_name)

    def draw(self) -> None:
        self.delete("all")
        width, height = max(self.winfo_width(), 240), 142
        active = bool(self.scene_name and self.app.macro_running and
                      self.app.current_macro_scene_name == self.scene_name)
        accent = GREEN if active else CYAN
        outline = accent if active or self.hover else LINE_BRIGHT
        fill = "#0C2A25" if active else ("#0D2531" if self.hover else PANEL)
        self.create_polygon(1, 1, width - 12, 1, width - 1, 12,
                            width - 1, height - 1, 12, height - 1,
                            1, height - 12, fill=fill, outline=outline,
                            width=2 if active or self.hover else 1)
        self.create_line(13, 7, 76, 7, fill=accent)
        self.create_line(width - 64, height - 7, width - 13, height - 7,
                         fill=accent)
        if self.scene_name is None:
            self.create_text(22, 31, anchor="w", text="＋ NEW MACRO",
                             fill=CYAN, font=("Courier", 12, "bold"))
            self.create_text(22, 60, anchor="w", text="CREATE EMPTY SCENE",
                             fill=MUTED, font=("Courier", 7, "bold"))
            self.create_text(22, 108, anchor="w", text="OPEN EDITOR",
                             fill=GREEN, font=("Courier", 8, "bold"))
            return

        summary = self.app._macro_scene_summary(self.scene_name)
        self.create_text(18, 23, anchor="w", text=self.scene_name,
                         fill=TEXT, font=("Courier", 10, "bold"))
        self.create_text(width - 18, 23, anchor="e",
                         text="RUNNING" if active else f"{summary['steps']:02d} STEPS",
                         fill=accent, font=("Courier", 7, "bold"))
        self.create_text(18, 48, anchor="w",
                         text=f"PLANNED  {summary['duration']:.1f} s",
                         fill=AMBER, font=("Courier", 7, "bold"))
        self.create_text(18, 67, anchor="w",
                         text=f"LAST RUN  {summary['last_run']}",
                         fill=MUTED, font=("Courier", 7))
        x = 18
        for robot_id in summary["robots"]:
            spec = self.app.states[robot_id].spec
            label = spec.name[:8]
            chip_width = max(42, len(label) * 6 + 13)
            self.create_rectangle(x, 80, x + chip_width, 96,
                                  fill="#091721", outline=spec.accent)
            self.create_text(x + chip_width / 2, 88, text=label,
                             fill=spec.accent, font=("Courier", 6, "bold"))
            x += chip_width + 6
            if x > width - 58:
                break
        self.create_rectangle(18, 108, 78, 130, fill="#0B2B25", outline=GREEN)
        self.create_text(48, 119, text="RUN", fill=GREEN,
                         font=("Courier", 7, "bold"))
        self.create_rectangle(width - 88, 108, width - 18, 130,
                              fill="#0A202A", outline=CYAN)
        self.create_text(width - 53, 119, text="EDIT", fill=CYAN,
                         font=("Courier", 7, "bold"))


class ControlDeck(tk.Tk):
    def __init__(self, backend=None) -> None:
        super().__init__()
        self.backend = backend or (SimulationRobotBackend() if "--simulate" in sys.argv else SpheroV2Backend())
        self.states: Dict[str, RobotState] = {spec.robot_id: RobotState(spec) for spec in ROBOT_SPECS}
        # Runtime order of successful BLE handshakes. Disconnected units are
        # removed; a later reconnection therefore appends them at the end of
        # the connected group.
        self.connection_order: List[str] = []
        self.connect_retry_counts: Dict[str, int] = {
            spec.robot_id: 0 for spec in ROBOT_SPECS
        }
        self.robot_tiles: Dict[str, RobotTile] = {}
        self.gesture_buttons: Dict[str, GestureButton] = {}
        self.standard_buttons: Dict[str, GestureButton] = {}
        self.led_buttons: Dict[tuple, LedToggle] = {}
        self.disconnect_buttons: Dict[str, LedToggle] = {}
        self.disconnect_busy: Set[str] = set()
        self.animation_busy: Set[str] = set()
        self.standard_queues = {spec.robot_id: queue.Queue() for spec in ROBOT_SPECS}
        self.standard_workers: Dict[str, threading.Thread] = {}
        self.standard_busy: Set[str] = set()
        self.led_busy: Set[tuple] = set()
        self.led_restore_jobs: Dict[str, str] = {}
        self.led_states: Dict[str, Dict[str, bool]] = {
            spec.robot_id: {channel: False for channel, _label in LED_CHANNELS.get(spec.model_id, ())}
            for spec in ROBOT_SPECS
        }
        self.animation_catalog: Dict[str, Set[str]] = {spec.robot_id: set() for spec in ROBOT_SPECS}
        self.orientation_data: Dict[str, Dict[str, Optional[float]]] = {}
        self.orientation_errors: Dict[str, str] = {}
        self.orientation_polling: Set[str] = set()
        self.next_orientation_poll = 0.0
        self.battery_polling: Set[str] = set()
        self.battery_errors: Dict[str, str] = {}
        self.next_battery_poll: Dict[str, float] = {
            spec.robot_id: 0.0 for spec in ROBOT_SPECS
        }
        self.activity_history: Dict[str, List[float]] = {
            spec.robot_id: [] for spec in ROBOT_SPECS
        }
        self.next_activity_redraw = 0.0
        self.tab_buttons: Dict[str, NeonButton] = {}
        self.tab_frames: Dict[str, tk.Frame] = {}
        self.after_jobs: Set[str] = set()
        self.last_clicked = "r2d2"
        self.current_tab = "log"
        self.preferred_selected: Set[str] = set()
        self.has_saved_selection = False
        self.auto_confirm = tk.BooleanVar(value=True)
        self.interval_min = tk.IntVar(value=1)
        self.interval_max = tk.IntVar(value=5)
        self.always_on_top = tk.BooleanVar(value=False)
        self.macro_export_email = tk.StringVar(value="")
        self.macro_name = tk.StringVar(value="SCENE 01")
        self.macro_scenes: Dict[str, List[dict]] = {}
        self.macro_metadata: Dict[str, Dict[str, str]] = {}
        self.current_macro_scene_name = "SCENE 01"
        self.macro_steps: List[dict] = []
        self.macro_run_steps: List[dict] = []
        self.macro_rows: List[tk.Frame] = []
        self.macro_active_rows: Set[int] = set()
        self.macro_queued_rows: Set[int] = set()
        self.macro_running = False
        self.macro_run_token = 0
        self.macro_pending = 0
        self.macro_dispatch_done = False
        self.macro_load_error = ""
        self.macro_editor_window = None
        self.macro_queues = {spec.robot_id: queue.Queue() for spec in ROBOT_SPECS}
        self.macro_workers: Dict[str, threading.Thread] = {}
        self.closing = False
        self.log_count = 0
        self.log_file_enabled = True
        self.log_file_error = ""
        self.shutdown_workers: List[threading.Thread] = []
        self.shutdown_errors: List[tuple] = []
        self.shutdown_deadline = 0.0
        self.power_suspended = False
        self.resume_in_progress = False
        self.resume_generation = 0
        self.resume_expected_ids: Set[str] = set()
        self.resume_scan_attempt = 0
        self.resume_retry_deadline = 0.0
        self.last_resume_event = 0.0
        self.last_clock_tick = time.monotonic()
        self.discovery_generation = 0
        self.discovery_in_progress = False
        self._power_hook_installed = False
        self._power_hwnd = None
        self._power_old_wndproc = None
        self._power_wndproc_callback = None
        self._initialize_log_file()
        self._load_settings()
        restore_known = getattr(self.backend, "restore_known_devices", None)
        if callable(restore_known):
            restore_known(self.settings.get("known_devices", {}))
        self._load_macro_file()
        if self.last_clicked in self.states:
            self.states[self.last_clicked].focused = True
        self._configure_window()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        self.bind("<Configure>", self._debounced_geometry_save)
        self._animate()
        self._update_clock()
        self.log("SYSTEM", f"Droids Control for Windows initialized / {self.backend.mode_label}", "ok")
        self.log("UI", self.icon_status, "ok" if self.window_icon else "warn")
        if self.log_file_error:
            self._log_error("LOG_FILE_INIT", self.log_file_error, path=LOG_PATH)
        if self.macro_load_error:
            self._log_error("MACRO_LOAD", self.macro_load_error, path=MACRO_PATH)
        self.log("BT SCAN", "Scanning for nearby droids…")
        self._start_discovery("startup")

    # ── Settings and window ──────────────────────────────────────────────────
    def _initialize_log_file(self) -> None:
        """Start one fresh diagnostic log for the current application run."""
        try:
            LOG_PATH.write_text("", encoding="utf-8")
        except Exception as exc:
            self.log_file_enabled = False
            self.log_file_error = exception_text(exc)

    def _append_log_file(self, line: str) -> str:
        if not self.log_file_enabled:
            return ""
        try:
            with LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(line)
            return ""
        except Exception as exc:
            self.log_file_enabled = False
            self.log_file_error = exception_text(exc)
            return self.log_file_error

    def _load_settings(self) -> None:
        self.settings = {}
        try:
            self.settings = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.settings = {}
        self.last_clicked = self.settings.get("last_clicked", "r2d2")
        self.current_tab = self.settings.get("current_tab", "log")
        self.auto_confirm.set(bool(self.settings.get("auto_confirm", True)))
        self.interval_min.set(int(self.settings.get("interval_min", 1)))
        self.interval_max.set(int(self.settings.get("interval_max", 5)))
        self.always_on_top.set(bool(self.settings.get("always_on_top", False)))
        self.macro_export_email.set(str(self.settings.get("macro_export_email", ""))[:160])
        if int(self.settings.get("selection_ui_version", 1)) < 2:
            # Previous versions toggled group selection on every tile click.
            # Reset once so accidental saved deselections do not survive the fix.
            self.has_saved_selection = False
            self.preferred_selected = set()
        else:
            self.has_saved_selection = "selected" in self.settings
            self.preferred_selected = set(self.settings.get("selected", []))
        saved_leds = self.settings.get("led_states", {})
        for robot_id, channels in self.led_states.items():
            robot_saved = saved_leds.get(robot_id, {})
            for channel in channels:
                channels[channel] = bool(robot_saved.get(channel, False))
        cutoff = time.time() - 20 * 60
        saved_activity = self.settings.get("activity_history", {})
        for robot_id in self.activity_history:
            self.activity_history[robot_id] = [
                float(stamp) for stamp in saved_activity.get(robot_id, [])
                if isinstance(stamp, (int, float)) and float(stamp) >= cutoff
            ]

    def _normalize_macro_steps(self, raw_steps) -> List[dict]:
        loaded = []
        if not isinstance(raw_steps, list):
            return loaded
        for raw in raw_steps:
            if not isinstance(raw, dict):
                continue
            robot_id = str(raw.get("robot_id", ""))
            kind = str(raw.get("kind", ""))
            gesture_id = str(raw.get("gesture_id", ""))
            if kind == "animation" and robot_id in self.states:
                model_id = self.states[robot_id].spec.model_id
                gesture_id = ANIMATION_ALIASES.get(model_id, {}).get(
                    gesture_id, gesture_id)
            if kind not in ("animation", "standard") or not gesture_id:
                kind, gesture_id = "", ""
            led_channel = str(raw.get("led_channel", ""))
            raw_led_enabled = raw.get("led_enabled", None)
            led_enabled = raw_led_enabled if isinstance(raw_led_enabled, bool) else None
            if robot_id not in self.states or (not gesture_id and not
                                                (led_channel and led_enabled is not None)):
                continue
            try:
                delay = max(0.5, min(5.0, float(raw.get("delay", 0.5))))
            except (TypeError, ValueError):
                delay = 0.5
            loaded.append({"robot_id": robot_id, "kind": kind,
                           "gesture_id": gesture_id,
                           "led_channel": led_channel,
                           "led_enabled": led_enabled,
                           "delay": delay})
        return loaded

    def _load_macro_file(self) -> None:
        if not MACRO_PATH.exists():
            self.macro_scenes = {"SCENE 01": []}
            self.macro_metadata = {"SCENE 01": {"last_run": ""}}
            return
        try:
            payload = json.loads(MACRO_PATH.read_text(encoding="utf-8"))
            scenes = {}
            metadata = {}
            raw_scenes = payload.get("scenes") if isinstance(payload, dict) else None
            if isinstance(raw_scenes, dict):
                for raw_name, raw_scene in raw_scenes.items():
                    name = str(raw_name).strip()[:60]
                    if not name:
                        continue
                    raw_steps = raw_scene.get("steps", []) if isinstance(raw_scene, dict) else raw_scene
                    scenes[name] = self._normalize_macro_steps(raw_steps)
                    metadata[name] = {
                        "last_run": str(raw_scene.get("last_run", ""))[:40]
                        if isinstance(raw_scene, dict) else ""
                    }
            else:
                # Transparent migration from the original one-scene JSON schema.
                name = str(payload.get("name", "SCENE 01")).strip()[:60] or "SCENE 01"
                scenes[name] = self._normalize_macro_steps(payload.get("steps", []))
                metadata[name] = {"last_run": ""}
            if not scenes:
                scenes = {"SCENE 01": []}
                metadata = {"SCENE 01": {"last_run": ""}}
            selected = str(payload.get("selected_scene", "")).strip()
            if selected not in scenes:
                selected = next(iter(scenes))
            self.macro_scenes = scenes
            self.macro_metadata = metadata
            self.current_macro_scene_name = selected
            self.macro_name.set(selected)
            self.macro_steps = [dict(step) for step in scenes[selected]]
        except Exception as exc:
            self.macro_scenes = {"SCENE 01": []}
            self.macro_metadata = {"SCENE 01": {"last_run": ""}}
            self.macro_load_error = exception_text(exc)

    def _persist_macro_scenes(self, report: bool = False) -> bool:
        payload = {"version": 4,
                   "selected_scene": self.current_macro_scene_name,
                   "scenes": {name: {
                       "steps": steps,
                       "last_run": self.macro_metadata.get(name, {}).get("last_run", "")
                   }
                              for name, steps in self.macro_scenes.items()}}
        try:
            MACRO_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            if report and hasattr(self, "log_text"):
                self.log("MACRO SAVE", f"{self.current_macro_scene_name} / "
                         f"{len(self.macro_steps)} steps / {len(self.macro_scenes)} scenes / "
                         f"{MACRO_PATH.name}", "ok")
            return True
        except Exception as exc:
            if hasattr(self, "log_text"):
                self._log_error("MACRO_SAVE", exception_text(exc), path=MACRO_PATH,
                                scene=self.current_macro_scene_name,
                                scene_count=len(self.macro_scenes),
                                step_count=len(self.macro_steps))
            return False

    def _save_macro_file(self, report: bool = False, explicit: bool = False) -> bool:
        target = (self.macro_name.get().strip()[:60] or
                  self.current_macro_scene_name or "SCENE 01")
        if explicit and target in self.macro_scenes:
            from tkinter import messagebox
            if not messagebox.askyesno(
                    "Overwrite scene?",
                    f'Scene "{target}" already exists. Overwrite its settings?',
                    parent=self):
                return False
        if not explicit:
            target = self.current_macro_scene_name or target
        self.macro_scenes[target] = [dict(step) for step in self.macro_steps]
        self.macro_metadata.setdefault(target, {"last_run": ""})
        self.current_macro_scene_name = target
        if explicit or not self.macro_name.get().strip():
            self.macro_name.set(target)
        saved = self._persist_macro_scenes(report)
        if hasattr(self, "macro_scene_menu"):
            self._refresh_macro_scene_menu()
        self._render_macro_matrix()
        return saved

    def _refresh_macro_scene_menu(self) -> None:
        if not hasattr(self, "macro_scene_menu"):
            return
        self.macro_scene_menu.delete(0, "end")
        self.macro_scene_menu.add_command(label="＋ ADD NEW / EMPTY",
                                          command=self._new_macro_scene)
        self.macro_scene_menu.add_separator()
        for name in self.macro_scenes:
            marker = "● " if name == self.current_macro_scene_name else "   "
            self.macro_scene_menu.add_command(
                label=f"{marker}{name}",
                command=lambda scene=name: self._select_macro_scene(scene))
        self.macro_scene_button.configure(text=f"SCENES / {len(self.macro_scenes)}  ▼")

    def _select_macro_scene(self, name: str) -> None:
        if self.macro_running or name not in self.macro_scenes:
            if self.macro_running and hasattr(self, "log_text"):
                self.log("MACRO BUSY", "Stop the running macro before changing scene", "warn")
            return
        self._save_macro_file()
        self.current_macro_scene_name = name
        self.macro_name.set(name)
        self.macro_steps = [dict(step) for step in self.macro_scenes[name]]
        self.macro_active_rows.clear()
        self.macro_queued_rows.clear()
        self._render_macro_rows()
        self._refresh_macro_scene_menu()
        if hasattr(self, "log_text"):
            self.log("MACRO LOAD", f"{name} / {len(self.macro_steps)} steps", "ok")

    def _new_macro_scene(self) -> None:
        if self.macro_running:
            if hasattr(self, "log_text"):
                self.log("MACRO BUSY", "Stop the running macro before creating a scene", "warn")
            return
        self._save_macro_file()
        number = 1
        while f"SCENE {number:02d}" in self.macro_scenes:
            number += 1
        self.current_macro_scene_name = ""
        self.macro_name.set(f"SCENE {number:02d}")
        self.macro_steps = []
        self.macro_active_rows.clear()
        self.macro_queued_rows.clear()
        self._render_macro_rows()
        self._refresh_macro_scene_menu()
        if hasattr(self, "log_text"):
            self.log("MACRO NEW", "Empty scene ready / enter a name and press SAVE", "ok")
        self._open_macro_editor(self.macro_name.get())

    def _save_macro_modal(self) -> None:
        from tkinter import messagebox
        target = self.macro_name.get().strip()[:60]
        if not target:
            messagebox.showwarning("Macro name required",
                                   "Enter a name for this macro scene.",
                                   parent=self.macro_editor_window or self)
            return
        old_name = self.current_macro_scene_name
        if target != old_name and target in self.macro_scenes:
            if not messagebox.askyesno(
                    "Overwrite macro?",
                    f'Macro "{target}" already exists. Overwrite it?',
                    parent=self.macro_editor_window or self):
                return
        metadata = dict(self.macro_metadata.get(old_name, {"last_run": ""}))
        if old_name and old_name != target:
            self.macro_scenes.pop(old_name, None)
            self.macro_metadata.pop(old_name, None)
        self.macro_scenes[target] = [dict(step) for step in self.macro_steps]
        self.macro_metadata[target] = metadata
        self.current_macro_scene_name = target
        self.macro_name.set(target)
        if self._persist_macro_scenes(report=True):
            if self.macro_editor_window is not None:
                self.macro_editor_window.title(f"Macro Editor — {target}")
            self._render_macro_matrix()

    def _delete_current_macro_scene(self) -> None:
        from tkinter import messagebox
        if self.macro_running:
            self.log("MACRO BUSY", "Stop the running macro before deleting it", "warn")
            return
        name = self.current_macro_scene_name
        if not name or name not in self.macro_scenes:
            self._close_macro_editor()
            return
        if not messagebox.askyesno(
                "Delete macro?",
                f'Delete macro "{name}" and all {len(self.macro_steps)} steps?\n\n'
                "This cannot be undone.",
                parent=self.macro_editor_window or self):
            return
        self.macro_scenes.pop(name, None)
        self.macro_metadata.pop(name, None)
        if self.macro_scenes:
            next_name = next(iter(self.macro_scenes))
            self.current_macro_scene_name = next_name
            self.macro_name.set(next_name)
            self.macro_steps = [dict(step) for step in self.macro_scenes[next_name]]
        else:
            self.current_macro_scene_name = "SCENE 01"
            self.macro_name.set("SCENE 01")
            self.macro_steps = []
            self.macro_scenes["SCENE 01"] = []
            self.macro_metadata["SCENE 01"] = {"last_run": ""}
        self._persist_macro_scenes()
        self.log("MACRO DELETE", f"{name} / scene removed", "warn")
        self._close_macro_editor()

    def _run_macro_scene(self, name: str) -> None:
        if self.macro_running:
            self.log("MACRO BUSY", "The macro is already running", "warn")
            return
        if name not in self.macro_scenes:
            return
        self.current_macro_scene_name = name
        self.macro_name.set(name)
        self.macro_steps = [dict(step) for step in self.macro_scenes[name]]
        self.run_macro()

    def _export_macros_email(self) -> None:
        from tkinter import messagebox
        email = self.macro_export_email.get().strip()[:160]
        if (not email or "@" not in email or email.startswith("@") or
                email.endswith("@") or "." not in email.split("@")[-1]):
            messagebox.showwarning("Email required",
                                   "Enter a valid destination email address.",
                                   parent=self)
            return
        self.macro_export_email.set(email)
        self._save_settings()
        self._save_macro_file()
        subject = "Droids Control for Windows macros JSON"
        body = "Drodis Control for Windows macro scenes for Android / iOS import."
        opened = False
        try:
            if sys.platform == "win32" and shutil.which("powershell.exe"):
                script = (
                    "$outlook=New-Object -ComObject Outlook.Application;"
                    "$mail=$outlook.CreateItem(0);"
                    "$mail.To=$args[0];$mail.Subject=$args[1];$mail.Body=$args[2];"
                    "$null=$mail.Attachments.Add($args[3]);$mail.Display()")
                subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", script,
                                  email, subject, body, str(MACRO_PATH.resolve())])
                opened = True
            elif sys.platform == "darwin" and shutil.which("osascript"):
                script = (
                    'on run argv\nset recipientAddress to item 1 of argv\n'
                    'set attachmentPath to item 2 of argv\n'
                    'tell application "Mail"\nset m to make new outgoing message with properties '
                    '{subject:"Droids Control for Windows macros JSON", content:"Droids Control for Windows macro scenes for Android / iOS import.", visible:true}\n'
                    'tell m\nmake new to recipient at end of to recipients with properties {address:recipientAddress}\n'
                    'make new attachment with properties {file name:POSIX file attachmentPath} at after the last paragraph\n'
                    'end tell\nactivate\nend tell\nend run')
                subprocess.Popen(["osascript", "-e", script, email,
                                  str(MACRO_PATH.resolve())])
                opened = True
            elif shutil.which("xdg-email"):
                subprocess.Popen(["xdg-email", "--subject", subject, "--body", body,
                                  "--attach", str(MACRO_PATH.resolve()), email])
                opened = True
        except Exception as exc:
            self._log_error("MACRO_EXPORT_EMAIL", exception_text(exc),
                            recipient=email, path=MACRO_PATH)
        if opened:
            self.log("MACRO EXPORT", f"Email draft opened for {email} / {MACRO_PATH.name}", "ok")
            messagebox.showinfo("Email draft ready",
                                "A message with r2d2_macro.json attached was opened.\n"
                                "Review it and press Send in your mail application.",
                                parent=self)
            return
        query = urlencode({"subject": subject, "body": body +
                           f"\n\nAttach this file manually: {MACRO_PATH.resolve()}"})
        webbrowser.open(f"mailto:{email}?{query}")
        try:
            if sys.platform == "win32":
                os.startfile(str(MACRO_PATH.parent))
        except OSError:
            pass
        self.log("MACRO EXPORT", f"Mail client opened / attach manually: {MACRO_PATH}", "warn")
        messagebox.showwarning(
            "Attach JSON manually",
            f"Your mail application does not support automatic attachments.\n\n"
            f"Attach this file:\n{MACRO_PATH.resolve()}", parent=self)

    def _configure_window(self) -> None:
        self.title("Droids Control for Windows")
        self.configure(bg=BG)
        # The window may collapse to the robot rail; the right workspace is
        # intentionally clipped when the user chooses a narrow width.
        self.minsize(320, 900)
        geometry = self.settings.get("geometry", "1460x960+70+40")
        self.geometry(geometry)
        self.attributes("-topmost", self.always_on_top.get())
        self._load_window_icon()
        # Native title-bar styling must run after Tk creates the platform window.
        self.after(10, self._apply_native_window_style)
        self.after(40, self._install_power_event_hook)

    def _load_window_icon(self) -> None:
        """Load iconw.png from source, packaged, executable, or launch directories."""
        script_dir = Path(__file__).resolve().parent
        candidates = [script_dir / "iconw.png"]
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.insert(0, Path(bundle_dir) / "iconw.png")
        if getattr(sys, "frozen", False):
            candidates.insert(0, Path(sys.executable).resolve().parent / "iconw.png")
        candidates.append(Path.cwd() / "iconw.png")

        self.window_icon = None
        self.icon_status = "iconw.png not found; using the operating-system fallback icon"
        checked = set()
        for icon_path in candidates:
            resolved = icon_path.resolve()
            if resolved in checked or not resolved.is_file():
                continue
            checked.add(resolved)
            try:
                self.window_icon = tk.PhotoImage(file=str(resolved))
                self.iconphoto(True, self.window_icon)
                self.icon_status = f"Window icon loaded: {resolved.name}"
                return
            except (OSError, tk.TclError) as exc:
                self.icon_status = f"Unable to load iconw.png: {exc}"

    def _apply_native_window_style(self, window=None) -> None:
        """Apply dark Windows DWM chrome while preserving native window controls."""
        if sys.platform != "win32":
            return
        try:
            import ctypes

            target = window or self
            target.update_idletasks()
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.GetParent.restype = ctypes.c_void_p
            client_hwnd = ctypes.c_void_p(target.winfo_id())
            parent_hwnd = user32.GetParent(client_hwnd)
            hwnd = ctypes.c_void_p(parent_hwnd or client_hwnd.value)
            dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
            dwm.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            dwm.restype = ctypes.c_long

            enabled = ctypes.c_int(1)
            # Attribute 20 is current; 19 supports older Windows 10 builds.
            if dwm(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)) != 0:
                dwm(hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))

            def colorref(hex_color: str):
                value = hex_color.lstrip("#")
                red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
                return ctypes.c_uint(red | (green << 8) | (blue << 16))

            # Windows 11 caption, text, and border colors. Unsupported attributes
            # fail silently on Windows 10 while immersive dark mode remains active.
            for attribute, color in ((35, PANEL_2), (36, TEXT), (34, LINE)):
                native_color = colorref(color)
                dwm(hwnd, attribute, ctypes.byref(native_color), ctypes.sizeof(native_color))

            shell32 = ctypes.windll.shell32
            shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
            shell32.SetCurrentProcessExplicitAppUserModelID("DroidsControlForWindows")
            flags = 0x0001 | 0x0002 | 0x0004 | 0x0020  # NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags)
        except Exception:
            # Keep the native system frame when DWM styling is unavailable.
            pass

    def _install_power_event_hook(self) -> None:
        """Receive Windows suspend/resume notifications on the Tk window."""
        if sys.platform != "win32" or self._power_hook_installed or self.closing:
            return
        try:
            import ctypes
            from ctypes import wintypes

            self.update_idletasks()
            user32 = ctypes.windll.user32
            client_hwnd = wintypes.HWND(self.winfo_id())
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            parent_hwnd = user32.GetParent(client_hwnd)
            hwnd = parent_hwnd or client_hwnd

            wndproc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                ctypes.c_size_t, ctypes.c_ssize_t)
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                                 ctypes.c_void_p]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                               wintypes.UINT, ctypes.c_size_t,
                                               ctypes.c_ssize_t]
            user32.CallWindowProcW.restype = ctypes.c_ssize_t

            wm_powerbroadcast = 0x0218
            pbt_apmsuspend = 0x0004
            pbt_apmresumeautomatic = 0x0012
            pbt_apmresumesuspend = 0x0007

            def power_wndproc(message_hwnd, message, wparam, lparam):
                if message == wm_powerbroadcast and not self.closing:
                    if wparam == pbt_apmsuspend:
                        self.after(0, lambda: self._handle_power_suspend(
                            "WM_POWERBROADCAST/PBT_APMSUSPEND"))
                    elif wparam in (pbt_apmresumeautomatic, pbt_apmresumesuspend):
                        event_name = ("PBT_APMRESUMEAUTOMATIC"
                                      if wparam == pbt_apmresumeautomatic
                                      else "PBT_APMRESUMESUSPEND")
                        self.after(0, lambda name=event_name:
                                   self._handle_power_resume(
                                       f"WM_POWERBROADCAST/{name}"))
                return user32.CallWindowProcW(
                    self._power_old_wndproc, message_hwnd, message, wparam, lparam)

            callback = wndproc_type(power_wndproc)
            ctypes.set_last_error(0)
            old_proc = user32.SetWindowLongPtrW(
                hwnd, -4, ctypes.cast(callback, ctypes.c_void_p))
            if not old_proc and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
            self._power_hwnd = hwnd
            self._power_old_wndproc = old_proc
            self._power_wndproc_callback = callback
            self._power_user32 = user32
            self._power_hook_installed = True
            self.log("POWER", "Windows suspend/resume monitor ready", "ok")
        except Exception as exc:
            self._log_error("POWER_MONITOR_INIT", exception_text(exc))

    def _uninstall_power_event_hook(self) -> None:
        if not self._power_hook_installed:
            return
        try:
            self._power_user32.SetWindowLongPtrW(
                self._power_hwnd, -4, self._power_old_wndproc)
        except Exception:
            pass
        self._power_hook_installed = False
        self._power_wndproc_callback = None

    def _cancel_robot_timers_for_power_event(self) -> None:
        for state in self.states.values():
            if state.timer_job:
                try:
                    self.after_cancel(state.timer_job)
                except tk.TclError:
                    pass
                state.timer_job = None
            state.next_confirmation = None

    def _discard_standard_queues_for_power_event(self) -> int:
        discarded = 0
        for work_queue in self.standard_queues.values():
            while True:
                try:
                    item = work_queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    work_queue.put(None)
                    break
                discarded += 1
        return discarded

    def _handle_power_suspend(self, source: str) -> None:
        if self.closing or self.power_suspended:
            return
        self.power_suspended = True
        self.resume_in_progress = False
        self.resume_generation += 1
        self.resume_expected_ids = {
            robot_id for robot_id, state in self.states.items()
            if state.connected or state.connecting
        }
        pause = getattr(self.backend, "pause_for_system_sleep", None)
        if callable(pause):
            pause()
        self._cancel_robot_timers_for_power_event()
        if self.macro_running:
            self.stop_macro()
        discarded = self._discard_standard_queues_for_power_event()
        expected = ", ".join(self.states[rid].spec.name
                             for rid in self.resume_expected_ids) or "none"
        self.log("POWER SUSPEND", f"Windows is suspending / preserving reconnect list: "
                 f"{expected} / queued commands cancelled={discarded} / source={source}", "warn")

    def _handle_power_resume(self, source: str) -> None:
        if self.closing:
            return
        now = time.monotonic()
        if self.resume_in_progress or now - self.last_resume_event < 5.0:
            return
        self.last_resume_event = now
        self.power_suspended = False
        self.resume_in_progress = True
        self.resume_generation += 1
        token = self.resume_generation
        if not self.resume_expected_ids:
            self.resume_expected_ids = {
                robot_id for robot_id, state in self.states.items()
                if state.connected or state.connecting or state.discovered
            }
        for robot_id, state in self.states.items():
            if state.connected:
                state.connection_stale = True
                state.connection_fault = "Windows resumed; BLE session awaiting reset"
            if state.selected:
                self.preferred_selected.add(robot_id)
        self._cancel_robot_timers_for_power_event()
        self._refresh_robot_ui()
        self.log("POWER RESUME", "Windows resumed / waiting 2.5 s for Bluetooth stack / "
                 f"source={source}", "warn")
        job = self.after(2500, lambda generation=token:
                         self._begin_resume_recovery(generation))
        self.after_jobs.add(job)

    def _begin_resume_recovery(self, generation: int) -> None:
        if (self.closing or generation != self.resume_generation or
                not self.resume_in_progress):
            return
        self.log("BLE SESSION RESET", "Closing pre-suspend BLE sessions without sleeping droids…",
                 "warn")
        threading.Thread(target=self._resume_reset_worker,
                         args=(generation,), daemon=True).start()

    def _resume_reset_worker(self, generation: int) -> None:
        result_queue = queue.Queue()

        def reset_one(robot_id: str) -> None:
            try:
                reset = getattr(self.backend, "reset_connection", None)
                if callable(reset):
                    reset(robot_id)
                else:
                    self.backend.disconnect(robot_id)
                    self.backend.forget(robot_id)
            except Exception as exc:
                result_queue.put((robot_id, exception_text(exc)))

        workers = [threading.Thread(target=reset_one, args=(robot_id,), daemon=True,
                                    name=f"ResumeReset-{robot_id}")
                   for robot_id in self.states]
        for worker in workers:
            worker.start()
        deadline = time.monotonic() + 12.0
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        errors = []
        while True:
            try:
                errors.append(result_queue.get_nowait())
            except queue.Empty:
                break
        for robot_id, worker in zip(self.states, workers):
            if worker.is_alive():
                errors.append((robot_id, "Timed out closing pre-suspend BLE session after 12 s"))
        if not self.closing:
            self.after(0, lambda token=generation, notes=errors:
                       self._finish_resume_reset(token, notes))

    def _finish_resume_reset(self, generation: int, errors: List[tuple]) -> None:
        if self.closing or generation != self.resume_generation:
            return
        for robot_id, error in errors:
            self._log_error("RESUME_SESSION_RESET", error, robot_id)
        for state in self.states.values():
            state.connected = False
            state.connecting = False
            state.connection_stale = False
            state.connection_fault = ""
            state.connection_failures = 0
            state.selected = False
            state.discovered = False
            state.battery = None
            state.battery_voltage = None
            state.battery_state = None
        self.connection_order.clear()
        self.disconnect_busy.clear()
        self.animation_busy.clear()
        self.standard_busy.clear()
        self.led_busy.clear()
        self.orientation_polling.clear()
        self.battery_polling.clear()
        resume_backend = getattr(self.backend, "resume_after_system_sleep", None)
        if callable(resume_backend):
            resume_backend()
        self.resume_scan_attempt = 1
        self._refresh_robot_ui()
        self.log("BT RESCAN", "Starting fresh Bluetooth scan after Windows resume…", "warn")
        self._start_discovery("resume")

    def _save_settings(self) -> None:
        if self.closing:
            geometry = self.settings.get("geometry", self.geometry())
        else:
            geometry = self.geometry()
        known_devices = self.settings.get("known_devices", {})
        read_known = getattr(self.backend, "known_devices", None)
        if callable(read_known):
            try:
                known_devices = read_known()
            except Exception:
                pass
        data = {
            "geometry": geometry,
            "selected": sorted(self.preferred_selected),
            "selection_ui_version": 2,
            "last_clicked": self.last_clicked,
            "current_tab": self.current_tab,
            "auto_confirm": self.auto_confirm.get(),
            "interval_min": self.interval_min.get(),
            "interval_max": self.interval_max.get(),
            "always_on_top": self.always_on_top.get(),
            "macro_export_email": self.macro_export_email.get().strip()[:160],
            "led_states": self.led_states,
            "activity_history": self.activity_history,
            "known_devices": known_devices,
        }
        self.settings = data
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _debounced_geometry_save(self, _event=None) -> None:
        old = getattr(self, "geometry_save_job", None)
        if old:
            try:
                self.after_cancel(old)
            except tk.TclError:
                pass
        self.geometry_save_job = self.after(700, self._save_settings)

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=24, pady=20)
        self._build_header(root)

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, pady=(18, 0))
        body.grid_columnconfigure(1, weight=1, minsize=0)
        body.grid_rowconfigure(0, weight=1)

        self._build_robot_rail(body)
        self._build_main_panel(body)
        self._build_footer(root)

    def _build_header(self, root) -> None:
        header = tk.Frame(root, bg=BG, height=78)
        header.pack(fill="x")
        header.pack_propagate(False)
        logo = tk.Canvas(header, width=52, height=52, bg=BG, highlightthickness=0)
        logo.pack(side="left", padx=(0, 8))
        logo.create_oval(4, 4, 48, 48, outline=LINE_BRIGHT)
        logo.create_arc(8, 8, 44, 44, start=18, extent=88, style="arc", outline=CYAN, width=3)
        logo.create_arc(8, 8, 44, 44, start=198, extent=58, style="arc", outline=BLUE, width=2)
        logo.create_polygon(26, 13, 37, 26, 26, 39, 15, 26, outline=CYAN, fill="#0C2330")
        logo.create_text(26, 26, text="R", fill=TEXT, font=("TkDefaultFont", 11, "bold"))
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left")
        tk.Label(brand, text="DROIDS CONTROL FOR WINDOWS", bg=BG, fg=TEXT,
                 font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        tk.Label(brand, text="ASTROMECH COMMAND NETWORK  /  SECURE BT MESH", bg=BG, fg=MUTED,
                 font=("TkDefaultFont", 7)).pack(anchor="w")

        scope = tk.Canvas(header, width=290, height=58, bg=BG, highlightthickness=0)
        scope.pack(side="left", padx=34)
        scope.create_polygon(1, 9, 9, 1, 281, 1, 289, 9, 289, 49, 281, 57, 9, 57, 1, 49,
                             fill="#08131C", outline=LINE)
        for x in range(12, 280, 18):
            scope.create_line(x, 11, x, 48, fill="#0D2836")
        for y in (18, 30, 42):
            scope.create_line(10, y, 280, y, fill="#0D2836")
        points = []
        for x in range(10, 281, 5):
            y = 31 + math.sin(x / 18) * 7 + math.sin(x / 6) * 2
            points.extend((x, y))
        scope.create_line(*points, fill=CYAN, width=2, smooth=True)
        scope.create_text(13, 8, text="BT SPECTRUM / LIVE", anchor="nw", fill=MUTED,
                          font=("Courier", 6, "bold"))
        scope.create_text(276, 47, text="2.402—2.480 GHz", anchor="se", fill=CYAN,
                          font=("Courier", 6))
        right = tk.Frame(header, bg=BG)
        right.pack(side="right")
        mode = tk.Frame(right, bg="#0B1B24", highlightbackground=LINE, highlightthickness=1)
        mode.pack(side="left", padx=18)
        mode_color = AMBER if self.backend.is_simulation else GREEN
        tk.Label(mode, text=f"●  {self.backend.mode_label}", bg="#0B1B24", fg=mode_color,
                 font=("TkDefaultFont", 8, "bold")).pack(padx=12, pady=(8, 2))
        tk.Label(mode, text="BACKEND / ADAPTER READY", bg="#0B1B24", fg=MUTED,
                 font=("Courier", 6)).pack(padx=12, pady=(0, 8))
        self.clock_label = tk.Label(right, text="00:00:00", bg=BG, fg=MUTED,
                                    font=("Courier", 12, "bold"))
        self.clock_label.pack(side="left")
        tk.Frame(root, bg=LINE, height=1).pack(fill="x")

    def _build_robot_rail(self, parent) -> None:
        rail = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1, width=252)
        rail.grid(row=0, column=0, sticky="nsw", padx=(0, 18))
        rail.grid_propagate(False)
        top = tk.Frame(rail, bg=PANEL)
        top.pack(fill="x", padx=14, pady=(15, 8))
        tk.Label(top, text="01–06 / UNITS", bg=PANEL, fg=MUTED,
                 font=("TkDefaultFont", 8, "bold")).pack(side="left")
        tk.Label(top, text="◌ BT", bg=PANEL, fg=CYAN,
                 font=("TkDefaultFont", 8, "bold")).pack(side="right")
        status = tk.Frame(rail, bg=PANEL)
        status.pack(side="bottom", fill="x", padx=14, pady=15)
        tk.Frame(status, bg=LINE, height=1).pack(fill="x", pady=(0, 12))
        self.scan_label = tk.Label(status, text="●  SCANNING FOR DROIDS", bg=PANEL, fg=CYAN,
                                   font=("TkDefaultFont", 7, "bold"))
        self.scan_label.pack(anchor="w")

        rail_canvas = tk.Canvas(rail, bg=PANEL, highlightthickness=0, width=250)
        rail_canvas.pack(fill="both", expand=True)
        rail_scroll = tech_scrollbar(rail, orient="vertical", command=rail_canvas.yview)
        rail_scroll.place(relx=1.0, rely=0.08, relheight=0.82, anchor="ne")
        rail_canvas.configure(yscrollcommand=rail_scroll.set)
        tile_host = tk.Frame(rail_canvas, bg=PANEL)
        tile_window = rail_canvas.create_window((0, 0), window=tile_host, anchor="nw", width=246)
        tile_host.bind("<Configure>", lambda _e: rail_canvas.configure(
            scrollregion=rail_canvas.bbox("all")))
        rail_canvas.bind("<Configure>", lambda e: rail_canvas.itemconfigure(tile_window, width=e.width))
        rail_canvas.bind("<Enter>", lambda _e: self.bind_all(
            "<MouseWheel>", lambda event: rail_canvas.yview_scroll(int(-event.delta / 120), "units")))
        rail_canvas.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))
        self.robot_tile_host = tile_host
        for spec in ROBOT_SPECS:
            tile = RobotTile(tile_host, self.states[spec.robot_id], self.on_robot_click)
            self.robot_tiles[spec.robot_id] = tile
        for robot_id in ROBOT_RAIL_ORDER:
            self.robot_tiles[robot_id].pack(padx=12, pady=4)

    def _ordered_robot_ids(self) -> List[str]:
        """Connected by handshake time, followed by offline units."""
        visible = [robot_id for robot_id in ROBOT_RAIL_ORDER
                   if robot_id in self.states]
        connected = [robot_id for robot_id in self.connection_order
                     if robot_id in visible and self.states[robot_id].connected
                     and not self.states[robot_id].connection_stale]
        # Defensive fallback for a backend that changes state outside the
        # normal connection callback.
        connected.extend(robot_id for robot_id in visible
                         if self.states[robot_id].connected
                         and not self.states[robot_id].connection_stale
                         and robot_id not in connected)
        offline = [robot_id for robot_id in visible
                   if not self.states[robot_id].connected
                   or self.states[robot_id].connection_stale]
        return connected + offline

    def _refresh_robot_tile_order(self) -> None:
        if not hasattr(self, "robot_tile_host"):
            return
        for robot_id in self._ordered_robot_ids():
            tile = self.robot_tiles[robot_id]
            tile.pack_forget()
            tile.pack(padx=12, pady=4)

    def _show_robot_tile(self, robot_id: str) -> None:
        if robot_id not in ROBOT_RAIL_ORDER:
            return
        tile = self.robot_tiles.get(robot_id)
        if tile and not tile.winfo_manager():
            tile.pack(padx=12, pady=4)
        self._refresh_robot_tile_order()

    def _robot_is_visible(self, robot_id: str) -> bool:
        state = self.states[robot_id]
        return state.spec.slot == 1 or state.discovered or state.connected

    def _build_main_panel(self, parent) -> None:
        panel = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        panel.grid(row=0, column=1, sticky="nsew")
        nav = tk.Frame(panel, bg=PANEL, height=61)
        nav.pack(fill="x", padx=22, pady=(12, 0))
        tabs = [("log", "SYSTEM LOG  01"), ("gestures", "GESTURE MATRIX  02"),
                ("telemetry", "TELEMETRY  03"), ("settings", "SETTINGS  04")]
        for tab_id, label in tabs:
            button = NeonButton(nav, label, lambda t=tab_id: self.switch_tab(t), width=148, height=35,
                                active=tab_id == self.current_tab)
            button.pack(side="left", padx=(0, 8), pady=6)
            self.tab_buttons[tab_id] = button
        tk.Frame(panel, bg=LINE, height=1).pack(fill="x")
        container = tk.Frame(panel, bg=PANEL)
        container.pack(fill="both", expand=True, padx=26, pady=22)
        for tab_id in ("log", "gestures", "telemetry", "settings"):
            frame = tk.Frame(container, bg=PANEL)
            self.tab_frames[tab_id] = frame
        self._build_log_tab(self.tab_frames["log"])
        self._build_gestures_tab(self.tab_frames["gestures"])
        self._build_telemetry_tab(self.tab_frames["telemetry"])
        self._build_settings_tab(self.tab_frames["settings"])
        self.tab_frames[self.current_tab].pack(fill="both", expand=True)

    def _title_block(self, parent, eyebrow: str, title: str) -> tk.Frame:
        block = tk.Frame(parent, bg=PANEL)
        block.pack(fill="x", pady=(0, 20))
        left = tk.Frame(block, bg=PANEL)
        left.pack(side="left")
        tk.Label(left, text=eyebrow, bg=PANEL, fg=CYAN,
                 font=("TkDefaultFont", 7, "bold")).pack(anchor="w")
        tk.Label(left, text=title, bg=PANEL, fg=TEXT,
                 font=("TkDefaultFont", 23, "bold")).pack(anchor="w", pady=(4, 0))
        return block

    def _build_log_tab(self, frame) -> None:
        block = self._title_block(frame, "LIVE EVENT STREAM / BT MESH", "System log")
        self.online_label = tk.Label(block, text="●  0 ONLINE", bg="#0B2631", fg=GREEN,
                                     padx=12, pady=8, font=("TkDefaultFont", 8, "bold"))
        self.online_label.pack(side="right", pady=6)
        toolbar = tk.Frame(frame, bg=PANEL_2, highlightbackground=LINE, highlightthickness=1)
        toolbar.pack(fill="x")
        tk.Label(toolbar, text="●  ALL EVENTS", bg=PANEL_2, fg=CYAN,
                 font=("TkDefaultFont", 7, "bold")).pack(side="left", padx=12, pady=9)
        tk.Button(toolbar, text="CLEAR", command=self.clear_log, bg=PANEL_2, fg=MUTED,
                  activebackground=PANEL_3, activeforeground=CYAN, relief="flat", bd=0,
                  cursor="hand2", font=("TkDefaultFont", 7, "bold")).pack(side="right", padx=10)
        stream = tk.Frame(frame, bg=PANEL)
        stream.pack(fill="both", expand=True, pady=(8, 0))
        log_frame = tk.Frame(stream, bg=PANEL)
        log_frame.pack(side="left", fill="both", expand=True)
        scrollbar = tech_scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(log_frame, bg=PANEL, fg=TEXT, insertbackground=CYAN,
                                selectbackground="#16475C", relief="flat", bd=0,
                                font=("Courier", 9), wrap="word", state="disabled",
                                yscrollcommand=scrollbar.set, padx=6, pady=6)
        self.log_text.pack(fill="both", expand=True)
        scrollbar.configure(command=self.log_text.yview)
        self.log_text.tag_configure("time", foreground="#58798C")
        self.log_text.tag_configure("type", foreground=CYAN)
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("error", foreground=RED)
        self.log_text.tag_configure("connected_line", foreground=GREEN,
                                    background="#0A241E")

        diagnostics = tk.Frame(stream, bg="#08131C", width=210,
                               highlightbackground=LINE, highlightthickness=1)
        diagnostics.pack(side="right", fill="y", padx=(10, 0))
        diagnostics.pack_propagate(False)
        tk.Label(diagnostics, text="MESH TOPOLOGY", bg="#08131C", fg=CYAN,
                 font=("Courier", 7, "bold")).pack(anchor="w", padx=13, pady=(14, 4))
        self.radar = tk.Canvas(diagnostics, width=182, height=184, bg="#08131C", highlightthickness=0)
        self.radar.pack(padx=12)
        for radius in (28, 52, 77):
            self.radar.create_oval(91-radius, 91-radius, 91+radius, 91+radius, outline="#174052")
        self.radar.create_line(14, 91, 168, 91, fill="#174052")
        self.radar.create_line(91, 14, 91, 168, fill="#174052")
        self.radar.create_text(91, 8, text="N", fill=MUTED, font=("Courier", 6, "bold"))
        self.radar.create_text(174, 91, text="E", fill=MUTED, font=("Courier", 6, "bold"))
        self.radar.create_text(8, 91, text="W", fill=MUTED, font=("Courier", 6, "bold"))
        self.radar.create_text(91, 177, text="RANGE 12.0 m", fill=MUTED, font=("Courier", 6))
        self.radar_robot_label = tk.Label(
            diagnostics, text="R2-D2 / ORIENTATION", bg="#08131C", fg=CYAN,
            font=("Courier", 9, "bold"), anchor="w")
        self.radar_robot_label.pack(fill="x", padx=13, pady=(3, 1))
        self.radar_data_label = tk.Label(
            diagnostics, text="OFFLINE / BEARING 323°\nATTITUDE DATA UNAVAILABLE",
            bg="#08131C", fg=MUTED, justify="left", anchor="w",
            font=("Courier", 8, "bold"))
        self.radar_data_label.pack(fill="x", padx=13, pady=(0, 7))
        self._draw_radar_focus()
        for title, value, color in (("PACKETS", "00482", CYAN), ("LATENCY", "018 ms", GREEN),
                                    ("DROPPED", "000.2%", AMBER), ("CHANNEL", "37 / LE", TEXT)):
            row = tk.Frame(diagnostics, bg="#08131C")
            row.pack(fill="x", padx=13, pady=5)
            tk.Label(row, text=title, bg="#08131C", fg=MUTED,
                     font=("Courier", 6)).pack(side="left")
            tk.Label(row, text=value, bg="#08131C", fg=color,
                     font=("Courier", 7, "bold")).pack(side="right")
        activity_panel = tk.Frame(diagnostics, bg="#08131C")
        activity_panel.pack(side="bottom", fill="x", padx=12, pady=12)
        self.activity_label = tk.Label(
            activity_panel, text="R2-D2 / ACTIVITY", bg="#08131C", fg=CYAN,
            font=("Courier", 7, "bold"), anchor="w")
        self.activity_label.pack(fill="x")
        self.activity_canvas = tk.Canvas(
            activity_panel, width=182, height=74, bg="#08131C", highlightthickness=0)
        self.activity_canvas.pack()
        self._draw_activity_chart()

    def _build_gestures_tab(self, frame) -> None:
        block = self._title_block(frame, "NATIVE API ENUMS / MULTI-CAST ENABLED", "Animation matrix")
        self.target_label = tk.Label(block, text="0 TARGETS", bg="#0B2631", fg=CYAN,
                                     padx=12, pady=8, font=("TkDefaultFont", 8, "bold"))
        self.target_label.pack(side="right", pady=6)
        tk.Label(frame, text="Commands are sent to selected robots. With no selection, the last-clicked unit is used.",
                 bg=PANEL, fg=MUTED, font=("TkDefaultFont", 8)).pack(anchor="w", pady=(0, 16))
        legend = tk.Frame(frame, bg=PANEL)
        legend.pack(fill="x", pady=(0, 8))
        for spec in ROBOT_SPECS:
            tk.Label(legend, text=f"■ {spec.name}", bg=PANEL, fg=spec.accent,
                     font=("Courier", 7, "bold")).pack(side="left", padx=(0, 18))

        tk.Label(frame, text="STANDARD MOVEMENT COMMANDS", bg=PANEL, fg=CYAN,
                 font=("Courier", 7, "bold")).pack(anchor="w", pady=(4, 4))
        tk.Label(frame,
                 text="◇ CENTER LEG SAFE  Verified dome-only command; no stance or leg command is issued.",
                 bg=PANEL, fg=GREEN, font=("Courier", 7, "bold")).pack(anchor="w", pady=(0, 4))
        standard_grid = tk.Frame(frame, bg=PANEL)
        standard_grid.pack(fill="x", pady=(0, 10))
        for col in range(4):
            standard_grid.grid_columnconfigure(col, weight=1, uniform="standard")
        for index, (command_id, title, description, icon, _models) in enumerate(STANDARD_COMMANDS):
            descriptor = (command_id, title, description, icon)
            button = GestureButton(standard_grid, descriptor, self.execute_standard)
            button.grid(row=index // 4, column=index % 4, sticky="nsew", padx=6, pady=4)
            self.standard_buttons[command_id] = button

        tk.Label(frame, text="NATIVE ANIMATION ENUMS", bg=PANEL, fg=CYAN,
                 font=("Courier", 7, "bold")).pack(anchor="w", pady=(0, 4))
        host = tk.Frame(frame, bg=PANEL)
        host.pack(fill="both", expand=True)
        self.gesture_canvas = tk.Canvas(host, bg=PANEL, highlightthickness=0)
        scrollbar = tech_scrollbar(host, orient="vertical", command=self.gesture_canvas.yview)
        self.gesture_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.gesture_canvas.pack(side="left", fill="both", expand=True)
        self.gesture_grid = tk.Frame(self.gesture_canvas, bg=PANEL)
        self.gesture_window = self.gesture_canvas.create_window((0, 0), window=self.gesture_grid, anchor="nw")
        self.gesture_grid.bind("<Configure>", lambda _e: self.gesture_canvas.configure(
            scrollregion=self.gesture_canvas.bbox("all")))
        self.gesture_canvas.bind("<Configure>", lambda e: self.gesture_canvas.itemconfigure(
            self.gesture_window, width=e.width))
        self.gesture_canvas.bind("<Enter>", lambda _e: self.bind_all("<MouseWheel>", self._scroll_gestures))
        self.gesture_canvas.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))
        tk.Label(self.gesture_grid, text="WAITING FOR API ANIMATION CATALOG…",
                 bg=PANEL, fg=MUTED, font=("Courier", 10, "bold")).pack(pady=80)

    def _scroll_gestures(self, event) -> None:
        self.gesture_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _apply_animation_catalog(self, catalog: Dict[str, List[str]]) -> None:
        for robot_id, state in self.states.items():
            names = catalog.get(state.spec.model_id, ())
            self.animation_catalog[robot_id] = set(names)
            state.animations = set(names)
        for child in self.gesture_grid.winfo_children():
            child.destroy()
        self.gesture_buttons.clear()
        all_names = sorted(set().union(*self.animation_catalog.values()))
        if not all_names:
            tk.Label(self.gesture_grid, text="NO ANIMATIONS EXPORTED BY DETECTED API",
                     bg=PANEL, fg=AMBER, font=("Courier", 10, "bold")).pack(pady=80)
            return
        for col in range(4):
            self.gesture_grid.grid_columnconfigure(col, weight=1, uniform="animations")
        for index, animation_name in enumerate(all_names):
            is_bb8_path = animation_name.startswith("BB8_PATH_")
            title = (animation_name.removeprefix("BB8_PATH_").replace("_", " ")
                     if is_bb8_path else animation_name.replace("_", " "))
            description = ("ORIGINAL BB-8 DRIVE PATH / KEYFRAME CONTROL"
                           if is_bb8_path else
                           "ORIGINAL SPHERO FIRMWARE ANIMATION / DID 23 CID 5")
            descriptor = (animation_name, title, description,
                          "◆" if is_bb8_path else "▶")
            button = GestureButton(self.gesture_grid, descriptor, self.execute_gesture)
            button.grid(row=index // 4, column=index % 4, sticky="nsew", padx=6, pady=6)
            self.gesture_buttons[animation_name] = button
        self._refresh_gestures()
        self._render_macro_rows()
        if getattr(self, "pending_macro_robot", ""):
            self._select_pending_macro_robot(self.pending_macro_robot)

    def _build_telemetry_tab(self, frame) -> None:
        self._title_block(frame, "SENSOR ARRAY / LIVE METRICS", "Telemetry")
        grid = tk.Frame(frame, bg=PANEL)
        grid.pack(fill="both", expand=True)
        self.telemetry_grid = grid
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1, uniform="telemetry_columns")
        for row in range(2):
            grid.grid_rowconfigure(row, weight=1, uniform="telemetry_rows")
        self.telemetry_link_labels = {}
        self.telemetry_id_labels = {}
        self.telemetry_battery_labels = {}
        self.telemetry_confirm_labels = {}
        self.telemetry_cards = {}
        for spec in ROBOT_SPECS:
            card = tk.Frame(grid, bg=PANEL_2, highlightbackground=LINE, highlightthickness=1)
            self.telemetry_cards[spec.robot_id] = card
            tk.Label(card, text=spec.name, bg=PANEL_2, fg=TEXT,
                     font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=11, pady=(9, 4))
            link_label = tk.Label(card, text="BLE LINK  OFFLINE", justify="left",
                                  bg=PANEL_2, fg=RED, font=("Courier", 8, "bold"))
            link_label.pack(anchor="w", padx=11, pady=1)
            id_label = tk.Label(card, text=f"API ID    {spec.robot_id.upper()} / BT ID —",
                                justify="left", bg=PANEL_2, fg=MUTED,
                                font=("Courier", 7, "bold"))
            id_label.pack(anchor="w", padx=11, pady=1)
            battery_label = tk.Label(card, text="BATTERY   WAITING", justify="left",
                                     bg=PANEL_2, fg=MUTED, font=("Courier", 8, "bold"))
            battery_label.pack(anchor="w", padx=11, pady=1)
            confirm_label = tk.Label(card, text="NEXT CONFIRM  —", justify="left",
                                     bg=PANEL_2, fg=MUTED, font=("Courier", 8))
            confirm_label.pack(anchor="w", padx=11, pady=1)
            self.telemetry_link_labels[spec.robot_id] = link_label
            self.telemetry_id_labels[spec.robot_id] = id_label
            self.telemetry_battery_labels[spec.robot_id] = battery_label
            self.telemetry_confirm_labels[spec.robot_id] = confirm_label
            led_panel = tk.Frame(card, bg=PANEL_2)
            led_panel.pack(fill="x", padx=8, pady=(5, 5))
            led_panel.configure(height=102)
            led_panel.grid_propagate(False)
            channels = LED_CHANNELS.get(spec.model_id, ())
            if channels:
                tk.Label(led_panel, text="LED CONTROL / CLICK TO TOGGLE", bg=PANEL_2, fg=spec.accent,
                         font=("Courier", 6, "bold")).grid(row=0, column=0, columnspan=2,
                                                           sticky="w", padx=4, pady=(0, 5))
                for button_index, (channel, title) in enumerate(channels):
                    button = LedToggle(led_panel, title, spec.accent,
                                       lambda rid=spec.robot_id, ch=channel: self.toggle_led(rid, ch))
                    button.grid(row=1 + button_index // 2, column=button_index % 2,
                                sticky="w", padx=4, pady=3)
                    self.led_buttons[(spec.robot_id, channel)] = button
            else:
                tk.Label(led_panel, text="NO INDEPENDENT LED CHANNELS EXPORTED",
                         bg=PANEL_2, fg=MUTED, font=("Courier", 6)).pack(anchor="w", padx=4, pady=8)
            action_panel = tk.Frame(card, bg=PANEL_2)
            action_panel.pack(fill="x", padx=10, pady=(0, 8))
            disconnect_button = LedToggle(
                action_panel, "FORCE DISCONNECT", RED,
                lambda rid=spec.robot_id: self.force_disconnect(rid), width=140, danger=True)
            disconnect_button.pack(anchor="w")
            self.disconnect_buttons[spec.robot_id] = disconnect_button
        self._refresh_telemetry_visibility()

    def _refresh_telemetry_visibility(self) -> None:
        if not hasattr(self, "telemetry_cards"):
            return
        for card in self.telemetry_cards.values():
            card.grid_forget()
        for index, robot_id in enumerate(self._ordered_robot_ids()):
            self.telemetry_cards[robot_id].grid(
                row=index // 3, column=index % 3, sticky="nsew", padx=5, pady=5)

    def _build_settings_tab(self, frame) -> None:
        self._title_block(frame, "PREFERENCES / LOCAL STORAGE", "Settings")
        card = tk.Frame(frame, bg=PANEL_2, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x")
        tk.Checkbutton(card, text="Keep app always on top", variable=self.always_on_top,
                       command=self.on_always_on_top_changed, bg=PANEL_2, fg=TEXT,
                       selectcolor=PANEL_3, activebackground=PANEL_2, activeforeground=CYAN,
                       font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=18, pady=(18, 10))
        tk.Checkbutton(card, text="Automatic connection confirmation movement", variable=self.auto_confirm,
                       command=self.on_settings_changed, bg=PANEL_2, fg=TEXT,
                       selectcolor=PANEL_3, activebackground=PANEL_2, activeforeground=CYAN,
                       font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=18, pady=(0, 10))
        row = tk.Frame(card, bg=PANEL_2)
        row.pack(fill="x", padx=18, pady=(4, 18))
        tk.Label(row, text="RANDOM INTERVAL", bg=PANEL_2, fg=MUTED,
                 font=("TkDefaultFont", 7, "bold")).pack(side="left")
        self.min_spin = tk.Spinbox(row, from_=1, to=15, width=3, textvariable=self.interval_min,
                                   command=self.on_settings_changed, bg=PANEL_3, fg=CYAN,
                                   buttonbackground=LINE_BRIGHT, relief="flat")
        self.min_spin.pack(side="left", padx=(20, 6))
        tk.Label(row, text="to", bg=PANEL_2, fg=MUTED).pack(side="left")
        self.max_spin = tk.Spinbox(row, from_=1, to=15, width=3, textvariable=self.interval_max,
                                   command=self.on_settings_changed, bg=PANEL_3, fg=CYAN,
                                   buttonbackground=LINE_BRIGHT, relief="flat")
        self.max_spin.pack(side="left", padx=6)
        tk.Label(row, text="minutes / separate timer for each unit", bg=PANEL_2, fg=MUTED,
                 font=("TkDefaultFont", 8)).pack(side="left", padx=6)
        note = tk.Frame(frame, bg=PANEL_3, highlightbackground=LINE, highlightthickness=1)
        note.pack(fill="x", pady=16)
        tk.Label(note, text="APPLICATION MEMORY", bg=PANEL_3, fg=CYAN,
                 font=("TkDefaultFont", 8, "bold")).pack(anchor="w", padx=16, pady=(14, 5))
        tk.Label(note, text="Window size and position, always-on-top mode, robot selection, active tab, timer settings,\nLED channel states, and the rolling 20-minute activity history are saved automatically.\nThe current session log is saved to r2d2_log.txt and replaced at the next launch.",
                 justify="left", bg=PANEL_3, fg=MUTED, font=("TkDefaultFont", 8)).pack(anchor="w", padx=16, pady=(0, 14))
        self._build_macro_matrix(frame)

    def _build_macro_matrix(self, frame) -> None:
        panel = tk.Frame(frame, bg=PANEL_2, highlightbackground=LINE_BRIGHT,
                         highlightthickness=1)
        panel.pack(fill="both", expand=True, pady=(0, 4))
        header = tk.Frame(panel, bg=PANEL_2)
        header.pack(fill="x", padx=14, pady=(11, 6))
        tk.Label(header, text="MACROS MATRIX", bg=PANEL_2, fg=CYAN,
                 font=("Courier", 11, "bold")).pack(side="left")
        self.macro_matrix_count = tk.Label(header, text="00 SCENES", bg=PANEL_2,
                                           fg=MUTED, font=("Courier", 7, "bold"))
        self.macro_matrix_count.pack(side="left", padx=14)

        export = tk.Frame(panel, bg="#0B1721", highlightbackground=LINE,
                          highlightthickness=1)
        export.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(export, text="JSON EXPORT / MOBILE TRANSFER", bg="#0B1721", fg=MUTED,
                 font=("Courier", 7, "bold")).pack(side="left", padx=(10, 8), pady=8)
        self.macro_email_entry = tk.Entry(
            export, textvariable=self.macro_export_email, width=34,
            bg=PANEL_3, fg=TEXT, insertbackground=CYAN, relief="flat",
            font=("Courier", 8))
        self.macro_email_entry.pack(side="left", padx=5, ipady=5)
        self.macro_email_entry.bind("<FocusOut>", lambda _e: self._save_settings())
        tk.Button(export, text="EXPORT EMAIL", command=self._export_macros_email,
                  bg="#12303B", fg=GREEN, activebackground="#194454",
                  activeforeground=TEXT, relief="flat", cursor="hand2",
                  font=("Courier", 7, "bold"), padx=10, pady=5).pack(
                      side="left", padx=7)
        tk.Button(export, text="＋ ADD NEW", command=self._new_macro_scene,
                  bg=PANEL_3, fg=CYAN, activebackground="#15303D",
                  activeforeground=TEXT, relief="flat", cursor="hand2",
                  font=("Courier", 7, "bold"), padx=10, pady=5).pack(
                      side="right", padx=8)

        matrix_area = tk.Frame(panel, bg=PANEL)
        matrix_area.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.macro_matrix_canvas = tk.Canvas(matrix_area, bg=PANEL,
                                             highlightthickness=0)
        matrix_scroll = tech_scrollbar(
            matrix_area, orient="vertical", command=self.macro_matrix_canvas.yview)
        self.macro_matrix_canvas.configure(yscrollcommand=matrix_scroll.set)
        matrix_scroll.pack(side="right", fill="y")
        self.macro_matrix_canvas.pack(side="left", fill="both", expand=True)
        self.macro_matrix_host = tk.Frame(self.macro_matrix_canvas, bg=PANEL)
        self.macro_matrix_window = self.macro_matrix_canvas.create_window(
            (0, 0), window=self.macro_matrix_host, anchor="nw")
        self.macro_matrix_host.bind("<Configure>", lambda _e:
                                    self.macro_matrix_canvas.configure(
                                        scrollregion=self.macro_matrix_canvas.bbox("all")))
        self.macro_matrix_canvas.bind("<Configure>", lambda e:
                                      self.macro_matrix_canvas.itemconfigure(
                                          self.macro_matrix_window, width=e.width))
        self._render_macro_matrix()

    def _macro_scene_summary(self, name: str) -> dict:
        steps = self.macro_scenes.get(name, [])
        robots = []
        for step in steps:
            robot_id = step.get("robot_id", "")
            if robot_id in self.states and robot_id not in robots:
                robots.append(robot_id)
        raw_last_run = self.macro_metadata.get(name, {}).get("last_run", "")
        last_run = "NEVER"
        if raw_last_run:
            try:
                last_run = datetime.fromisoformat(raw_last_run).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                last_run = raw_last_run[:16]
        return {"steps": len(steps),
                "duration": sum(float(step.get("delay", 0.5)) for step in steps),
                "robots": robots,
                "last_run": last_run}

    def _render_macro_matrix(self) -> None:
        host = getattr(self, "macro_matrix_host", None)
        if host is None:
            return
        try:
            if not host.winfo_exists():
                return
        except tk.TclError:
            return
        for child in host.winfo_children():
            child.destroy()
        for column in range(3):
            host.grid_columnconfigure(column, weight=1, uniform="macro_matrix")
        names = list(self.macro_scenes)
        cards = [(name, MacroCard(host, self, name)) for name in names]
        cards.append((None, MacroCard(host, self, None)))
        for index, (_name, card) in enumerate(cards):
            card.grid(row=index // 3, column=index % 3, sticky="nsew",
                      padx=5, pady=5)
        if hasattr(self, "macro_matrix_count"):
            self.macro_matrix_count.configure(text=f"{len(names):02d} SCENES")

    def _open_macro_editor(self, name: str) -> None:
        if self.macro_running and name != self.current_macro_scene_name:
            self.log("MACRO BUSY", "Stop the running macro before editing another scene", "warn")
            return
        window = getattr(self, "macro_editor_window", None)
        try:
            if window is not None and window.winfo_exists():
                window.destroy()
        except tk.TclError:
            pass
        if name in self.macro_scenes:
            self.current_macro_scene_name = name
            self.macro_name.set(name)
            self.macro_steps = [dict(step) for step in self.macro_scenes[name]]
        modal = tk.Toplevel(self)
        self.macro_editor_window = modal
        modal.title(f"Macro Editor — {self.macro_name.get()}")
        modal.configure(bg=BG)
        modal.transient(self)
        modal.grab_set()
        modal.geometry("1180x650")
        modal.minsize(980, 520)
        if self.window_icon:
            modal.iconphoto(True, self.window_icon)
        modal.attributes("-topmost", self.always_on_top.get())
        modal.protocol("WM_DELETE_WINDOW", self._close_macro_editor)
        self._build_macro_editor(modal)
        self._center_modal(modal)
        modal.after(10, lambda: self._apply_native_window_style(modal))
        modal.after(20, self.macro_name_entry.focus_set)
        self.log("MACRO EDIT", f"{self.macro_name.get()} / editor opened", "ok")

    def _center_modal(self, modal) -> None:
        self.update_idletasks()
        modal.update_idletasks()
        width = max(980, modal.winfo_width())
        height = max(520, modal.winfo_height())
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        modal.geometry(f"{width}x{height}+{x}+{y}")

    def _close_macro_editor(self) -> None:
        window = getattr(self, "macro_editor_window", None)
        self.macro_editor_window = None
        if window is not None:
            try:
                window.grab_release()
                window.destroy()
            except tk.TclError:
                pass
        self._render_macro_matrix()

    def _build_macro_editor(self, frame) -> None:
        panel = tk.Frame(frame, bg=PANEL_2, highlightbackground=LINE_BRIGHT, highlightthickness=1)
        panel.pack(fill="both", expand=True, pady=(0, 4))
        header = tk.Frame(panel, bg=PANEL_2)
        header.pack(fill="x", padx=14, pady=(12, 7))
        tk.Label(header, text="MACRO EDITOR / SEQUENTIAL CONTROL", bg=PANEL_2, fg=CYAN,
                 font=("Courier", 8, "bold")).pack(side="left")
        self.macro_name_entry = tk.Entry(header, textvariable=self.macro_name, width=20,
                                         bg=PANEL_3, fg=TEXT, insertbackground=CYAN,
                                         relief="flat", font=("Courier", 8, "bold"))
        self.macro_name_entry.pack(side="left", padx=12, ipady=5)
        for title, command, color in (("CLOSE", self._close_macro_editor, MUTED),
                                      ("DELETE", self._delete_current_macro_scene, RED),
                                      ("SAVE", self._save_macro_modal, CYAN),
                                      ("RUN", self.run_macro, GREEN),
                                      ("STOP", self.stop_macro, RED)):
            tk.Button(header, text=title, command=command, bg=PANEL_3, fg=color,
                      activebackground="#15303D", activeforeground=TEXT, relief="flat",
                      cursor="hand2", font=("Courier", 7, "bold"), padx=9, pady=5).pack(
                          side="right", padx=(5, 0))

        builder = tk.Frame(panel, bg="#0B1721", highlightbackground=LINE, highlightthickness=1)
        builder.pack(fill="x", padx=14, pady=(0, 8))
        self.pending_macro_robot = ""
        self.pending_macro_gesture = ""
        self.pending_macro_kind = ""
        self.pending_macro_led_channel = ""
        self.pending_macro_led_enabled = None
        self.pending_macro_delay = tk.DoubleVar(value=0.5)
        self.macro_robot_text = tk.StringVar(value="ADD ROBOT")
        self.macro_gesture_text = tk.StringVar(value="GESTURE / OPTIONAL")
        self.macro_led_text = tk.StringVar(value="LED / OPTIONAL")
        self.macro_robot_button = tk.Menubutton(
            builder, textvariable=self.macro_robot_text, bg=PANEL_3, fg=CYAN,
            activebackground="#15303D", activeforeground=TEXT, relief="flat",
            font=("Courier", 7, "bold"), width=21)
        self.macro_robot_button.pack(side="left", padx=(8, 5), pady=8)
        self.macro_robot_menu = tk.Menu(self.macro_robot_button, tearoff=False, bg=PANEL_3, fg=TEXT)
        self.macro_robot_button.configure(menu=self.macro_robot_menu)
        self.macro_gesture_button = tk.Menubutton(
            builder, textvariable=self.macro_gesture_text, bg=PANEL_3, fg=AMBER,
            activebackground="#15303D", activeforeground=TEXT, relief="flat",
            font=("Courier", 7, "bold"), width=25, state="disabled")
        self.macro_gesture_button.pack(side="left", padx=5, pady=8)
        self.macro_gesture_menu = tk.Menu(self.macro_gesture_button, tearoff=False, bg=PANEL_3, fg=TEXT)
        self.macro_gesture_button.configure(menu=self.macro_gesture_menu)
        self.macro_led_button = tk.Menubutton(
            builder, textvariable=self.macro_led_text, bg=PANEL_3, fg=GREEN,
            activebackground="#15303D", activeforeground=TEXT, relief="flat",
            font=("Courier", 7, "bold"), width=27, state="disabled")
        self.macro_led_button.pack(side="left", padx=5, pady=8)
        self.macro_led_menu = tk.Menu(self.macro_led_button, tearoff=False, bg=PANEL_3, fg=TEXT)
        self.macro_led_button.configure(menu=self.macro_led_menu)
        tk.Label(builder, text="SET DELAY", bg="#0B1721", fg=MUTED,
                 font=("Courier", 7, "bold")).pack(side="left", padx=(9, 3))
        tk.Spinbox(builder, from_=0.5, to=5.0, increment=0.5, width=4,
                   textvariable=self.pending_macro_delay, bg=PANEL_3, fg=TEXT,
                   buttonbackground=LINE_BRIGHT, relief="flat").pack(side="left", padx=4)
        tk.Label(builder, text="sec", bg="#0B1721", fg=MUTED,
                 font=("Courier", 7)).pack(side="left")
        tk.Button(builder, text="SAVE STEP", command=self._add_macro_step,
                  bg="#12303B", fg=GREEN, activebackground="#194454", relief="flat",
                  cursor="hand2", font=("Courier", 7, "bold"), padx=10).pack(
                      side="right", padx=8, pady=8)

        tk.Label(panel,
                 text="Steps run in strict order. A step may contain a gesture, one LED change, or both.",
                 bg=PANEL_2, fg=MUTED, font=("Courier", 7)).pack(anchor="w", padx=15, pady=(0, 5))

        rows_host = tk.Frame(panel, bg=PANEL_2)
        rows_host.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.macro_canvas = tk.Canvas(rows_host, bg=PANEL_2, highlightthickness=0, height=180)
        macro_scroll = tech_scrollbar(rows_host, orient="vertical", command=self.macro_canvas.yview)
        self.macro_canvas.configure(yscrollcommand=macro_scroll.set)
        macro_scroll.pack(side="right", fill="y")
        self.macro_canvas.pack(side="left", fill="both", expand=True)
        self.macro_rows_host = tk.Frame(self.macro_canvas, bg=PANEL_2)
        self.macro_rows_window = self.macro_canvas.create_window(
            (0, 0), window=self.macro_rows_host, anchor="nw")
        self.macro_rows_host.bind("<Configure>", lambda _e: self.macro_canvas.configure(
            scrollregion=self.macro_canvas.bbox("all")))
        self.macro_canvas.bind("<Configure>", lambda e: self.macro_canvas.itemconfigure(
            self.macro_rows_window, width=e.width))
        self._refresh_macro_robot_menu()
        self._render_macro_rows()

    def _macro_robot_label(self, robot_id: str) -> str:
        state = self.states[robot_id]
        suffix = ("STALE" if state.connection_stale else
                  ("ONLINE" if state.connected else "OFFLINE"))
        return f"{state.spec.name} / {suffix}"

    def _macro_gesture_options(self, robot_id: str) -> List[tuple]:
        state = self.states[robot_id]
        options = []
        for command_id, title, _description, _icon, models in STANDARD_COMMANDS:
            if state.spec.model_id in models:
                options.append(("standard", command_id, title))
        options.extend(("animation", name, name.replace("_", " "))
                       for name in sorted(state.animations))
        return options

    def _macro_gesture_label(self, kind: str, gesture_id: str) -> str:
        if not gesture_id:
            return "NO GESTURE"
        if kind == "standard":
            command = next((item for item in STANDARD_COMMANDS if item[0] == gesture_id), None)
            return command[1] if command else gesture_id.replace("_", " ").upper()
        return gesture_id.replace("_", " ")

    def _macro_led_options(self, robot_id: str) -> List[tuple]:
        state = self.states[robot_id]
        prefix = state.spec.name
        options = []
        for channel, title in LED_CHANNELS.get(state.spec.model_id, ()):
            options.append((channel, True, f"{prefix} / {title} / ON"))
            options.append((channel, False, f"{prefix} / {title} / OFF"))
        return options

    def _macro_led_label(self, robot_id: str, channel: str,
                         enabled: Optional[bool]) -> str:
        if not channel or enabled is None:
            return "NO LED CHANGE"
        title = next((label for key, label in LED_CHANNELS.get(
            self.states[robot_id].spec.model_id, ()) if key == channel), channel.upper())
        return f"{self.states[robot_id].spec.name} / {title} / {'ON' if enabled else 'OFF'}"

    def _macro_step_description(self, step: dict) -> str:
        actions = []
        if step.get("gesture_id"):
            actions.append(self._macro_gesture_label(
                step.get("kind", ""), step.get("gesture_id", "")))
        if step.get("led_channel") and step.get("led_enabled") is not None:
            actions.append(self._macro_led_label(
                step.get("robot_id", ""), step.get("led_channel", ""),
                step.get("led_enabled")))
        return " + ".join(actions) if actions else "NO ACTION"

    def _refresh_macro_robot_menu(self) -> None:
        menu = getattr(self, "macro_robot_menu", None)
        if menu is None:
            return
        try:
            if not menu.winfo_exists():
                return
        except tk.TclError:
            return
        menu.delete(0, "end")
        connected = [state for state in self.states.values()
                     if state.connected and not state.connection_stale]
        if not connected:
            menu.add_command(label="NO CONNECTED ROBOTS", state="disabled")
        for state in connected:
            rid = state.spec.robot_id
            menu.add_command(label=self._macro_robot_label(rid),
                             command=lambda value=rid: self._select_pending_macro_robot(value))

    def _select_pending_macro_robot(self, robot_id: str) -> None:
        self.pending_macro_robot = robot_id
        self.pending_macro_gesture = ""
        self.pending_macro_kind = ""
        self.pending_macro_led_channel = ""
        self.pending_macro_led_enabled = None
        self.macro_robot_text.set(self._macro_robot_label(robot_id))
        self.macro_gesture_text.set("NO GESTURE")
        self.macro_led_text.set("NO LED CHANGE")
        self.macro_gesture_menu.delete(0, "end")
        self.macro_gesture_menu.add_command(
            label="NO GESTURE", command=self._clear_pending_macro_gesture)
        self.macro_gesture_menu.add_separator()
        options = self._macro_gesture_options(robot_id)
        for kind, gesture_id, label in options:
            self.macro_gesture_menu.add_command(
                label=label, command=lambda k=kind, gid=gesture_id, text=label:
                self._select_pending_macro_gesture(k, gid, text))
        self.macro_gesture_button.configure(state="normal")
        self.macro_led_menu.delete(0, "end")
        self.macro_led_menu.add_command(label=f"FOR {self.states[robot_id].spec.name}",
                                        state="disabled")
        self.macro_led_menu.add_separator()
        self.macro_led_menu.add_command(
            label="NO LED CHANGE", command=self._clear_pending_macro_led)
        led_options = self._macro_led_options(robot_id)
        if led_options:
            self.macro_led_menu.add_separator()
        for channel, enabled, label in led_options:
            self.macro_led_menu.add_command(
                label=label, command=lambda ch=channel, value=enabled, text=label:
                self._select_pending_macro_led(ch, value, text))
        self.macro_led_button.configure(state="normal")

    def _clear_pending_macro_gesture(self) -> None:
        self.pending_macro_kind = ""
        self.pending_macro_gesture = ""
        self.macro_gesture_text.set("NO GESTURE")

    def _select_pending_macro_gesture(self, kind: str, gesture_id: str, label: str) -> None:
        self.pending_macro_kind = kind
        self.pending_macro_gesture = gesture_id
        self.macro_gesture_text.set(label)

    def _clear_pending_macro_led(self) -> None:
        self.pending_macro_led_channel = ""
        self.pending_macro_led_enabled = None
        self.macro_led_text.set("NO LED CHANGE")

    def _select_pending_macro_led(self, channel: str, enabled: bool, label: str) -> None:
        self.pending_macro_led_channel = channel
        self.pending_macro_led_enabled = bool(enabled)
        self.macro_led_text.set(label)

    def _add_macro_step(self) -> None:
        if self.macro_running:
            self.log("MACRO BUSY", "Stop the running macro before editing its steps", "warn")
            return
        has_gesture = bool(self.pending_macro_kind and self.pending_macro_gesture)
        has_led = bool(self.pending_macro_led_channel and
                       self.pending_macro_led_enabled is not None)
        if not self.pending_macro_robot or not (has_gesture or has_led):
            self.log("MACRO WARN", "Select a connected robot and at least a gesture or LED change", "warn")
            return
        delay = max(0.5, min(5.0, float(self.pending_macro_delay.get())))
        self.macro_steps.append({"robot_id": self.pending_macro_robot,
                                 "kind": self.pending_macro_kind,
                                 "gesture_id": self.pending_macro_gesture,
                                 "led_channel": self.pending_macro_led_channel,
                                 "led_enabled": self.pending_macro_led_enabled,
                                 "delay": delay})
        self._save_macro_file()
        self._render_macro_rows()
        self.log("MACRO STEP", f"Added step {len(self.macro_steps):02d} / "
                 f"{self._macro_robot_label(self.pending_macro_robot)} / "
                 f"{self.macro_gesture_text.get()} / {self.macro_led_text.get()} / "
                 f"{delay:.1f}s", "ok")

    def _render_macro_rows(self) -> None:
        host = getattr(self, "macro_rows_host", None)
        if host is None:
            return
        try:
            if not host.winfo_exists():
                return
        except tk.TclError:
            return
        for child in host.winfo_children():
            child.destroy()
        self.macro_rows = []
        if not self.macro_steps:
            tk.Label(host, text="NO MACRO STEPS / USE THE CONTROLS ABOVE",
                     bg=PANEL_2, fg=MUTED, font=("Courier", 8, "bold")).pack(pady=28)
            return
        for index, step in enumerate(self.macro_steps):
            active = index in self.macro_active_rows
            queued = index in self.macro_queued_rows and not active
            accent = self.states[step["robot_id"]].spec.accent
            row = tk.Frame(host,
                           bg="#102631" if active else ("#17202A" if queued else "#0B1721"),
                           highlightbackground=accent if active else (AMBER if queued else LINE),
                           highlightthickness=2 if active or queued else 1)
            row.pack(fill="x", pady=2)
            self.macro_rows.append(row)
            tk.Label(row, text=f"{index + 1:02d}", width=3, bg=row["bg"],
                     fg=accent,
                     font=("Courier", 8, "bold")).pack(
                         side="left", padx=(6, 2), pady=6)
            if queued:
                tk.Label(row, text="QUEUED", bg=row["bg"], fg=AMBER,
                         font=("Courier", 6, "bold")).pack(side="left", padx=(0, 3))

            robot_text = tk.StringVar(value=self._macro_robot_label(step["robot_id"]))
            robot_button = tk.Menubutton(row, textvariable=robot_text, width=17,
                                         bg=PANEL_3, fg=accent, relief="flat",
                                         font=("Courier", 7, "bold"))
            robot_menu = tk.Menu(robot_button, tearoff=False, bg=PANEL_3, fg=TEXT)
            for spec in ROBOT_SPECS:
                robot_menu.add_command(
                    label=self._macro_robot_label(spec.robot_id),
                    command=lambda i=index, rid=spec.robot_id: self._update_macro_step_robot(i, rid))
            robot_button.configure(menu=robot_menu)
            robot_button.pack(side="left", padx=3, pady=5)

            gesture_text = tk.StringVar(value=self._macro_gesture_label(
                step.get("kind", ""), step.get("gesture_id", "")))
            gesture_button = tk.Menubutton(row, textvariable=gesture_text, width=20,
                                           bg=PANEL_3, fg=accent, relief="flat",
                                           font=("Courier", 7, "bold"))
            gesture_menu = tk.Menu(gesture_button, tearoff=False, bg=PANEL_3, fg=accent)
            gesture_menu.add_command(
                label="NO GESTURE",
                command=lambda i=index: self._update_macro_step_gesture(i, "", ""))
            gesture_menu.add_separator()
            for kind, gesture_id, label in self._macro_gesture_options(step["robot_id"]):
                gesture_menu.add_command(
                    label=label, command=lambda i=index, k=kind, gid=gesture_id:
                    self._update_macro_step_gesture(i, k, gid))
            gesture_button.configure(menu=gesture_menu)
            gesture_button.pack(side="left", padx=3, pady=5)

            led_text = tk.StringVar(value=self._macro_led_label(
                step["robot_id"], step.get("led_channel", ""), step.get("led_enabled")))
            led_button = tk.Menubutton(row, textvariable=led_text, width=26,
                                       bg=PANEL_3, fg=accent, relief="flat",
                                       font=("Courier", 7, "bold"))
            led_menu = tk.Menu(led_button, tearoff=False, bg=PANEL_3, fg=accent)
            led_menu.add_command(label=f"FOR {self.states[step['robot_id']].spec.name}",
                                 state="disabled")
            led_menu.add_separator()
            led_menu.add_command(
                label="NO LED CHANGE",
                command=lambda i=index: self._update_macro_step_led(i, "", None))
            led_options = self._macro_led_options(step["robot_id"])
            if led_options:
                led_menu.add_separator()
            for channel, enabled, label in led_options:
                led_menu.add_command(
                    label=label, command=lambda i=index, ch=channel, value=enabled:
                    self._update_macro_step_led(i, ch, value))
            led_button.configure(menu=led_menu)
            led_button.pack(side="left", padx=3, pady=5)

            delay_var = tk.DoubleVar(value=float(step["delay"]))
            delay_box = tk.Spinbox(row, from_=0.5, to=5.0, increment=0.5, width=4,
                                   textvariable=delay_var, bg=PANEL_3, fg=accent,
                                   buttonbackground=LINE_BRIGHT, relief="flat",
                                   command=lambda i=index, var=delay_var:
                                   self._update_macro_step_delay(i, var))
            delay_box.pack(side="left", padx=3)
            delay_box.bind("<FocusOut>", lambda _e, i=index, var=delay_var:
                           self._update_macro_step_delay(i, var))
            tk.Label(row, text="s", bg=row["bg"], fg=accent,
                     font=("Courier", 7)).pack(side="left")
            for text, command, color in (
                    ("↑", lambda i=index: self._move_macro_step(i, -1), CYAN),
                    ("↓", lambda i=index: self._move_macro_step(i, 1), CYAN),
                    ("⧉", lambda i=index: self._copy_macro_step(i), accent),
                    ("×", lambda i=index: self._delete_macro_step(i), RED)):
                tk.Button(row, text=text, command=command, bg=PANEL_3, fg=color,
                          relief="flat", cursor="hand2", width=2,
                          font=("Courier", 8, "bold")).pack(side="right", padx=2, pady=5)

    def _macro_can_edit(self) -> bool:
        if self.macro_running:
            self.log("MACRO BUSY", "Stop the running macro before editing its steps", "warn")
            return False
        return True

    def _update_macro_step_robot(self, index: int, robot_id: str) -> None:
        if not self._macro_can_edit() or index >= len(self.macro_steps):
            return
        step = self.macro_steps[index]
        step["robot_id"] = robot_id
        valid = {(kind, gid) for kind, gid, _label in self._macro_gesture_options(robot_id)}
        if step.get("gesture_id") and (step.get("kind"), step.get("gesture_id")) not in valid:
            step["kind"], step["gesture_id"] = "", ""
        valid_leds = {channel for channel, _title in LED_CHANNELS.get(
            self.states[robot_id].spec.model_id, ())}
        if step.get("led_channel") not in valid_leds:
            step["led_channel"], step["led_enabled"] = "", None
        self._save_macro_file()
        self._render_macro_rows()

    def _update_macro_step_gesture(self, index: int, kind: str, gesture_id: str) -> None:
        if not self._macro_can_edit() or index >= len(self.macro_steps):
            return
        self.macro_steps[index].update(kind=kind, gesture_id=gesture_id)
        self._save_macro_file()
        self._render_macro_rows()

    def _update_macro_step_led(self, index: int, channel: str,
                               enabled: Optional[bool]) -> None:
        if not self._macro_can_edit() or index >= len(self.macro_steps):
            return
        self.macro_steps[index].update(
            led_channel=channel,
            led_enabled=bool(enabled) if enabled is not None else None)
        self._save_macro_file()
        self._render_macro_rows()

    def _update_macro_step_delay(self, index: int, variable: tk.DoubleVar) -> None:
        if not self._macro_can_edit() or index >= len(self.macro_steps):
            return
        try:
            delay = max(0.5, min(5.0, float(variable.get())))
        except (tk.TclError, ValueError):
            delay = 0.5
        self.macro_steps[index]["delay"] = delay
        variable.set(delay)
        self._save_macro_file()

    def _move_macro_step(self, index: int, direction: int) -> None:
        if not self._macro_can_edit():
            return
        target = index + direction
        if 0 <= index < len(self.macro_steps) and 0 <= target < len(self.macro_steps):
            self.macro_steps[index], self.macro_steps[target] = (
                self.macro_steps[target], self.macro_steps[index])
            self._save_macro_file()
            self._render_macro_rows()

    def _delete_macro_step(self, index: int) -> None:
        if not self._macro_can_edit() or not (0 <= index < len(self.macro_steps)):
            return
        self.macro_steps.pop(index)
        self._save_macro_file()
        self._render_macro_rows()

    def _copy_macro_step(self, index: int) -> None:
        if not self._macro_can_edit() or not (0 <= index < len(self.macro_steps)):
            return
        duplicate = dict(self.macro_steps[index])
        self.macro_steps.append(duplicate)
        self._save_macro_file()
        self._render_macro_rows()
        self.log("MACRO COPY", f"Step {index + 1:02d} copied to step "
                 f"{len(self.macro_steps):02d}", "ok")

    def _macro_step_valid(self, step: dict) -> tuple:
        robot_id = step.get("robot_id", "")
        state = self.states.get(robot_id)
        if state is None:
            return False, "robot slot does not exist"
        if not state.connected:
            return False, "robot is not connected"
        if state.connection_stale:
            return False, "BLE session is stale; click the robot tile to reconnect"
        gesture_valid, gesture_reason = self._macro_gesture_valid(step, state)
        led_valid, led_reason = self._macro_led_valid(step, state)
        present = [valid for valid in (gesture_valid, led_valid) if valid is not None]
        if not present:
            return False, "step contains neither a gesture nor an LED change"
        if any(present):
            return True, ""
        reasons = [reason for reason in (gesture_reason, led_reason) if reason]
        return False, "; ".join(reasons) or "no valid action"

    def _macro_gesture_valid(self, step: dict, state: RobotState) -> tuple:
        kind, gesture_id = step.get("kind"), step.get("gesture_id")
        if not gesture_id:
            return None, ""
        if kind == "animation":
            return (gesture_id in state.animations,
                    "animation is not exported for this robot model")
        if kind == "standard":
            command = next((item for item in STANDARD_COMMANDS if item[0] == gesture_id), None)
            return (bool(command and state.spec.model_id in command[4]),
                    "standard movement is not supported by this robot model")
        return False, "unknown gesture kind"

    def _macro_led_valid(self, step: dict, state: RobotState) -> tuple:
        channel = step.get("led_channel", "")
        enabled = step.get("led_enabled", None)
        if not channel and enabled is None:
            return None, ""
        valid_channels = {key for key, _label in LED_CHANNELS.get(state.spec.model_id, ())}
        return (bool(channel in valid_channels and isinstance(enabled, bool)),
                "LED channel/state is not supported by this robot model")

    def run_macro(self) -> None:
        if self.macro_running:
            self.log("MACRO BUSY", "The macro is already running", "warn")
            return
        if not self.macro_steps:
            self.log("MACRO WARN", "The macro has no steps", "warn")
            return
        self._save_macro_file()
        scene_name = self.current_macro_scene_name or self.macro_name.get().strip()
        if scene_name:
            self.macro_metadata.setdefault(scene_name, {})["last_run"] = (
                datetime.now().isoformat(timespec="seconds"))
            self._persist_macro_scenes()
        self.macro_running = True
        self.macro_run_token += 1
        self.macro_pending = 0
        self.macro_dispatch_done = False
        self.macro_active_rows.clear()
        self.macro_queued_rows.clear()
        self.macro_run_steps = [dict(step) for step in self.macro_steps]
        token = self.macro_run_token
        self._render_macro_matrix()
        self.log("MACRO RUN", f"{self.macro_name.get()} / {len(self.macro_run_steps)} steps", "ok")
        self._dispatch_macro_step(0, token)

    def _dispatch_macro_step(self, index: int, token: int) -> None:
        if self.closing or not self.macro_running or token != self.macro_run_token:
            return
        if index >= len(self.macro_run_steps):
            self.macro_dispatch_done = True
            self._finish_macro_if_ready(token)
            return
        step = dict(self.macro_run_steps[index])
        valid, reason = self._macro_step_valid(step)
        if not valid:
            state = self.states.get(step.get("robot_id", ""))
            robot_name = state.spec.name if state else step.get("robot_id", "UNKNOWN")
            self.log("MACRO SKIP", f"step={index + 1:02d} / {robot_name} / "
                     f"{self._macro_step_description(step)} / {reason}",
                     "warn")
            job = self.after(0, lambda i=index + 1, run=token:
                             self._dispatch_macro_step(i, run))
            self.after_jobs.add(job)
        else:
            self.macro_pending += 1
            self.macro_queued_rows.add(index)
            self._render_macro_rows()
            robot_id = step["robot_id"]
            self._ensure_macro_worker(robot_id)
            self.macro_queues[robot_id].put((token, index, step))
            # Dispatch timing belongs to the timeline, not to command
            # completion. Different robots can therefore overlap after 0.5 s,
            # while each robot's own queue still preserves its step order.
            delay_ms = int(max(0.5, min(5.0, float(step.get("delay", 0.5)))) * 1000)
            job = self.after(delay_ms, lambda i=index + 1, run=token:
                             self._dispatch_macro_step(i, run))
            self.after_jobs.add(job)

    def _ensure_macro_worker(self, robot_id: str) -> None:
        worker = self.macro_workers.get(robot_id)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(target=self._macro_robot_worker,
                                  args=(robot_id,), daemon=True)
        self.macro_workers[robot_id] = worker
        worker.start()

    def _macro_robot_worker(self, robot_id: str) -> None:
        work_queue = self.macro_queues[robot_id]
        while not self.closing:
            try:
                item = work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                return
            token, index, step = item
            if token != self.macro_run_token:
                continue
            if not self.closing:
                self.after(0, lambda i=index, run=token: self._mark_macro_step_active(i, run))
            success = False
            error = ""
            skipped = False
            try:
                valid, reason = self._macro_step_valid(step)
                if not valid:
                    skipped = True
                    error = reason
                else:
                    state = self.states[robot_id]
                    gesture_valid, gesture_reason = self._macro_gesture_valid(step, state)
                    led_valid, led_reason = self._macro_led_valid(step, state)
                    errors = []
                    gesture_applied = False
                    led_applied = False
                    if gesture_valid is False:
                        errors.append(f"gesture={gesture_reason}")
                    elif gesture_valid is True:
                        try:
                            if step["kind"] == "animation":
                                gesture_applied = bool(self.backend.execute_animation(
                                    robot_id, step["gesture_id"]))
                            else:
                                gesture_applied = bool(self.backend.execute_standard(
                                    robot_id, step["gesture_id"]))
                            if not gesture_applied:
                                errors.append("gesture=backend rejected command")
                        except Exception as exc:
                            errors.append(f"gesture={exception_text(exc)}")
                    if led_valid is False:
                        errors.append(f"led={led_reason}")
                    elif led_valid is True:
                        try:
                            led_applied = bool(self.backend.set_led(
                                robot_id, step["led_channel"], step["led_enabled"]))
                            if not led_applied:
                                errors.append("led=backend rejected command")
                        except Exception as exc:
                            errors.append(f"led={exception_text(exc)}")
                    step["_gesture_applied"] = gesture_applied
                    step["_led_applied"] = led_applied
                    success = not errors and (gesture_applied or led_applied)
                    error = "; ".join(errors)
            except Exception as exc:
                error = exception_text(exc)
            if not self.closing:
                self.after(0, lambda i=index, run=token, ok=success, err=error,
                           data=step, was_skipped=skipped:
                           self._finish_macro_step(i, run, data, ok, err, was_skipped))

    def _mark_macro_step_active(self, index: int, token: int) -> None:
        if self.macro_running and token == self.macro_run_token:
            self.macro_queued_rows.discard(index)
            self.macro_active_rows.add(index)
            self._render_macro_rows()
            canvas = getattr(self, "macro_canvas", None)
            try:
                if (canvas is not None and canvas.winfo_exists()
                        and 0 <= index < len(getattr(self, "macro_rows", []))):
                    canvas.yview_moveto(max(
                        0.0, index / max(1, len(self.macro_run_steps))))
            except tk.TclError:
                pass

    def _finish_macro_step(self, index: int, token: int, step: dict,
                           success: bool, error: str, skipped: bool = False) -> None:
        if token != self.macro_run_token:
            return
        self.macro_queued_rows.discard(index)
        self.macro_active_rows.discard(index)
        self.macro_pending = max(0, self.macro_pending - 1)
        state = self.states.get(step["robot_id"])
        if step.get("_led_applied") and state:
            channel = step.get("led_channel", "")
            if channel in self.led_states.get(step["robot_id"], {}):
                self.led_states[step["robot_id"]][channel] = bool(step.get("led_enabled"))
                self._refresh_led_ui()
        if skipped:
            robot_name = state.spec.name if state else step.get("robot_id", "UNKNOWN")
            self.log("MACRO SKIP", f"step={index + 1:02d} / {robot_name} / {error}", "warn")
        elif success and state:
            description = self._macro_step_description(step)
            self.log("MACRO DONE", f"step={index + 1:02d} / "
                     f"{state.spec.name} / {description}", "ok")
            self._record_activity(step["robot_id"])
        else:
            self._log_error("MACRO_STEP", error or "unknown macro execution failure",
                            step.get("robot_id"), macro=self.macro_name.get(),
                            step=index + 1, kind=step.get("kind"),
                            gesture=step.get("gesture_id"),
                            led_channel=step.get("led_channel"),
                            led_enabled=step.get("led_enabled"),
                            gesture_applied=step.get("_gesture_applied", False),
                            led_applied=step.get("_led_applied", False),
                            delay=step.get("delay"))
            if state:
                self._mark_connection_stale(
                    step["robot_id"], error or "unknown macro execution failure",
                    "MACRO_STEP")
        self._render_macro_rows()
        self._finish_macro_if_ready(token)

    def _finish_macro_if_ready(self, token: int) -> None:
        if (self.macro_running and token == self.macro_run_token
                and self.macro_dispatch_done and self.macro_pending == 0):
            self.macro_running = False
            self.macro_active_rows.clear()
            self.macro_queued_rows.clear()
            self.macro_run_steps = []
            self._render_macro_rows()
            self._render_macro_matrix()
            self.log("MACRO END", f"{self.macro_name.get()} / sequence completed", "ok")

    def stop_macro(self) -> None:
        if not self.macro_running:
            return
        self.macro_running = False
        self.macro_run_token += 1
        self.macro_pending = 0
        self.macro_dispatch_done = True
        self.macro_active_rows.clear()
        self.macro_queued_rows.clear()
        self.macro_run_steps = []
        for work_queue in self.macro_queues.values():
            try:
                while True:
                    work_queue.get_nowait()
            except queue.Empty:
                pass
        self._render_macro_rows()
        self._render_macro_matrix()
        self.log("MACRO STOP", f"{self.macro_name.get()} / queued steps cancelled", "warn")

    def _build_footer(self, root) -> None:
        tk.Frame(root, bg=LINE, height=1).pack(fill="x", pady=(15, 0))
        footer = tk.Frame(root, bg=BG)
        footer.pack(fill="x", pady=(10, 0))
        tk.Label(footer, text="DROIDS CONTROL FOR WINDOWS  v1.0", bg=BG, fg=MUTED,
                 font=("TkDefaultFont", 7)).pack(side="left")
        tk.Label(footer, text="●  BT ADAPTER READY", bg=BG, fg=GREEN,
                 font=("TkDefaultFont", 7, "bold")).pack(side="right")

    # ── Application behavior ─────────────────────────────────────────────────
    def switch_tab(self, tab_id: str) -> None:
        self.tab_frames[self.current_tab].pack_forget()
        self.tab_buttons[self.current_tab].set_active(False)
        self.current_tab = tab_id
        self.tab_frames[tab_id].pack(fill="both", expand=True)
        self.tab_buttons[tab_id].set_active(True)
        self._save_settings()

    def on_robot_click(self, robot_id: str, toggle_group: bool = False) -> None:
        state = self.states[robot_id]
        self.last_clicked = robot_id
        self.next_orientation_poll = 0.0
        for other in self.states.values():
            other.focused = other.spec.robot_id == robot_id
        if state.connected and state.connection_stale:
            self._recover_stale_connection(robot_id)
        elif state.connected:
            if toggle_group:
                state.selected = not state.selected
                if state.selected:
                    self.preferred_selected.add(robot_id)
                else:
                    self.preferred_selected.discard(robot_id)
                action = "added to group" if state.selected else "removed from group"
                self.log("SELECT", f"{state.spec.name} — {action}")
            else:
                group_status = "group target ON" if state.selected else "group target OFF"
                self.log("FOCUS", f"{state.spec.name} / focused / {group_status}")
        else:
            if state.connecting:
                self.log("BT LINK", f"{state.spec.name} / connection attempt already in progress")
            else:
                self.connect_retry_counts[robot_id] = 0
                self.log("BT RETRY", f"{state.spec.name} / forced scan and connection attempt", "warn")
                self._connect_robot(robot_id)
        self._draw_radar_focus()
        self._draw_activity_chart()
        self._refresh_robot_ui()
        self._save_settings()

    @staticmethod
    def _is_transport_failure(error: str) -> bool:
        lowered = (error or "").lower()
        return any(marker in lowered for marker in (
            "timeouterror", "timed out", "timeout", "no firmware acknowledgement",
            "not connected", "device with address", "bleakerror"))

    def _mark_connection_stale(self, robot_id: str, error: str,
                               operation: str) -> None:
        state = self.states.get(robot_id)
        if (not state or not state.connected or
                not self._is_transport_failure(error)):
            return
        state.connection_failures += 1
        lowered = error.lower()
        hard_disconnect = any(marker in lowered for marker in (
            "not connected", "device with address", "was not found",
            "bleakdevicenotfounderror"))
        # A firmware command can time out while a later queued command succeeds.
        # Require two consecutive transport failures unless the adapter states
        # explicitly that the device/session is gone.
        if not hard_disconnect and state.connection_failures < 2:
            return
        first_fault = not state.connection_stale
        state.connection_stale = True
        state.connection_fault = error
        state.selected = False
        self.preferred_selected.discard(robot_id)
        if state.timer_job:
            try:
                self.after_cancel(state.timer_job)
            except tk.TclError:
                pass
            state.timer_job = None
            state.next_confirmation = None
        if robot_id in self.connection_order:
            self.connection_order.remove(robot_id)
        if first_fault:
            self.log("BT STALE", f"{state.spec.name} / {operation} timed out / "
                     "click the robot tile to reset and reconnect", "error")
            dropped = 0
            work_queue = self.standard_queues.get(robot_id)
            if work_queue is not None:
                while True:
                    try:
                        queued = work_queue.get_nowait()
                    except queue.Empty:
                        break
                    if queued is None:
                        work_queue.put(None)
                        break
                    dropped += 1
            if dropped:
                self.log("QUEUE DROP", f"{state.spec.name} / cancelled {dropped} "
                         "queued movement commands after stale session", "warn")
        self._refresh_robot_ui()
        self._save_settings()

    def _recover_stale_connection(self, robot_id: str) -> None:
        state = self.states[robot_id]
        if robot_id in self.disconnect_busy or state.connecting:
            self.log("BT RECOVERY", f"{state.spec.name} / recovery already in progress", "warn")
            return
        self.disconnect_busy.add(robot_id)
        state.connecting = True
        self.log("BT RECOVERY", f"{state.spec.name} / dropping stale BLE session…", "warn")
        self._refresh_robot_ui()
        threading.Thread(target=self._stale_recovery_worker,
                         args=(robot_id,), daemon=True).start()

    def _stale_recovery_worker(self, robot_id: str) -> None:
        error = ""
        try:
            reset = getattr(self.backend, "reset_connection", None)
            if callable(reset):
                reset(robot_id)
            else:
                self.backend.disconnect(robot_id)
                self.backend.forget(robot_id)
        except Exception as exc:
            error = exception_text(exc)
        if not self.closing:
            self.after(0, lambda rid=robot_id, details=error:
                       self._finish_force_disconnect(rid, details, reconnect=True))

    def _start_discovery(self, reason: str = "manual") -> None:
        self.discovery_generation += 1
        generation = self.discovery_generation
        self.discovery_in_progress = True
        threading.Thread(target=self._discover_worker,
                         args=(generation, reason), daemon=True).start()

    def _discover_worker(self, generation: int, reason: str) -> None:
        try:
            found = self.backend.discover()
            catalog = self.backend.get_animation_catalog()
            if not self.closing:
                self.after(0, lambda token=generation, why=reason:
                           self._finish_discovery(token, why, found, catalog))
        except Exception as exc:
            if not self.closing:
                error = exception_text(exc)
                self.after(0, lambda e=error, token=generation, why=reason:
                           self._discovery_failed(e, token, why))

    def _finish_discovery(self, generation: int, reason: str,
                          found: List[str], catalog: Dict[str, List[str]]) -> None:
        if generation != self.discovery_generation or self.closing:
            return
        self.discovery_in_progress = False
        self._process_discovered(found, catalog, reason)

    def _resume_retry_when_idle(self) -> None:
        if self.closing or not self.resume_in_progress:
            return
        connecting = any(state.connecting for state in self.states.values())
        if connecting and time.monotonic() < self.resume_retry_deadline:
            job = self.after(2000, self._resume_retry_when_idle)
            self.after_jobs.add(job)
            return
        self._start_discovery("resume-retry")

    def _discovery_failed(self, error: str, generation: Optional[int] = None,
                          reason: str = "manual") -> None:
        if generation is not None and generation != self.discovery_generation:
            return
        self.discovery_in_progress = False
        self.scan_label.configure(text="●  BT SCAN FAILED", fg=RED)
        self._log_error("BLUETOOTH_SCAN", error, timeout="10.0s", reason=reason)
        if reason.startswith("resume"):
            self.resume_in_progress = False

    def _process_discovered(self, robot_ids: List[str], catalog: Dict[str, List[str]],
                            reason: str = "manual") -> None:
        self._apply_animation_catalog(catalog)
        counts = ", ".join(f"{self.states[rid].spec.name}:{len(names)}"
                           for rid, names in catalog.items() if rid in self.states)
        self.log("API ENUM", f"Animations loaded / {counts}", "ok")
        diagnostics = getattr(self.backend, "scan_diagnostics", None)
        if callable(diagnostics):
            report = diagnostics()
            if report:
                self.log("BLE RAW", report, "ok" if robot_ids else "warn")
        self.scan_label.configure(text=f"●  {len(robot_ids)} UNITS IN RANGE", fg=GREEN)
        for index, robot_id in enumerate(robot_ids):
            if robot_id not in self.states:
                continue
            state = self.states[robot_id]
            state.discovered = True
            self._show_robot_tile(robot_id)
            self.log("DETECTED", f"{state.spec.name} / identified as {state.spec.robot_type}", "ok")
            job = self.after(300 + index * 480, lambda rid=robot_id: self._connect_robot(rid))
            self.after_jobs.add(job)
        self._refresh_robot_ui()
        if reason.startswith("resume"):
            missing = self.resume_expected_ids.difference(robot_ids)
            if missing and self.resume_scan_attempt < 2:
                self.resume_scan_attempt += 1
                names = ", ".join(self.states[rid].spec.name for rid in missing)
                self.log("BT RESCAN", f"Still missing after resume: {names} / "
                         "one additional scan after current handshakes", "warn")
                self.resume_retry_deadline = time.monotonic() + 30.0
                job = self.after(3000, self._resume_retry_when_idle)
                self.after_jobs.add(job)
            else:
                self.resume_in_progress = False
                if missing:
                    names = ", ".join(self.states[rid].spec.name for rid in missing)
                    self.log("WAKE REQUIRED", f"Not advertising after Windows resume: {names} / "
                             "wake physically, place on charger, or use the Sphero app", "error")
                else:
                    self.log("BT RESCAN", "Resume scan completed / reconnect cycle started", "ok")

    def _draw_radar_focus(self) -> None:
        if not hasattr(self, "radar"):
            return
        radar = self.radar
        radar.delete("focus")
        robot_id = self.last_clicked if self.last_clicked in self.states else "r2d2"
        if not self._robot_is_visible(robot_id):
            robot_id = model_id_for(robot_id)
        state = self.states[robot_id]
        sample = self.orientation_data.get(robot_id, {})
        measured = sample.get("direction")
        direction = measured if measured is not None else RADAR_FALLBACK_HEADINGS[robot_id]
        direction %= 360.0

        # The highlighted sector and bearing line always belong to the last tile
        # clicked by the user. Live yaw replaces the diagram's fallback bearing.
        radar.create_arc(14, 14, 168, 168, start=90.0-direction-22.0, extent=44,
                         style="pieslice", fill="#0B2A36", outline=state.spec.accent,
                         width=2, tags="focus")
        angle = math.radians(direction)
        focus_x = 91 + math.sin(angle) * 60
        focus_y = 91 - math.cos(angle) * 60
        radar.create_line(91, 91, focus_x, focus_y, fill=state.spec.accent,
                          width=2, arrow="last", tags="focus")

        short_model = {"r2d2": "R2", "r2q5": "Q5", "bb8e": "8E", "bb9e": "9E"}
        labels = {spec.robot_id: f"{short_model[spec.model_id]}·{spec.slot}" for spec in ROBOT_SPECS}
        for other_id, other in self.states.items():
            if not self._robot_is_visible(other_id):
                continue
            bearing = (direction if other_id == robot_id
                       else RADAR_FALLBACK_HEADINGS[other_id])
            point_angle = math.radians(bearing)
            x = 91 + math.sin(point_angle) * 58
            y = 91 - math.cos(point_angle) * 58
            radius = 6 if other_id == robot_id else 4
            radar.create_oval(x-radius, y-radius, x+radius, y+radius,
                              fill=other.spec.accent,
                              outline=TEXT if other_id == robot_id else LINE_BRIGHT,
                              width=2 if other_id == robot_id else 1, tags="focus")
            radar.create_text(x+8, y-8, text=labels[other_id], fill=other.spec.accent,
                              anchor="w", font=("Courier", 7, "bold"), tags="focus")

        self.radar_robot_label.configure(
            text=f"{state.spec.name} / ORIENTATION", fg=state.spec.accent)
        if not state.connected or state.connection_stale:
            self.radar_data_label.configure(
                text=(f"STALE SESSION / BEARING {direction:05.1f}°\nCLICK TILE TO RECONNECT"
                      if state.connection_stale else
                      f"OFFLINE / BEARING {direction:05.1f}°\nATTITUDE DATA UNAVAILABLE"),
                fg=RED if state.connection_stale else MUTED)
            return
        if not sample:
            status = ("SENSOR READ ERROR / SEE SYSTEM LOG"
                      if robot_id in self.orientation_errors else "WAITING FOR SENSOR DATA")
            self.radar_data_label.configure(text=f"BEARING {direction:05.1f}°\n{status}", fg=AMBER)
            return

        def value(name: str) -> str:
            number = sample.get(name)
            return "N/A" if number is None else f"{number:+06.1f}°"

        source = "LIVE YAW" if sample.get("yaw") is not None else "API HEADING"
        self.radar_data_label.configure(
            text=(f"DIRECTION {direction:05.1f}° / {source}\n"
                  f"YAW {value('yaw')}  PITCH {value('pitch')}\n"
                  f"ROLL {value('roll')}  HEAD {value('heading')}"),
            fg=TEXT)

    def _record_activity(self, robot_id: str) -> None:
        now = time.time()
        cutoff = now - 20 * 60
        history = self.activity_history.setdefault(robot_id, [])
        history[:] = [stamp for stamp in history if stamp >= cutoff]
        history.append(now)
        if robot_id == self.last_clicked:
            self._draw_activity_chart(now)
        self._save_settings()

    def _draw_activity_chart(self, now: Optional[float] = None) -> None:
        if not hasattr(self, "activity_canvas"):
            return
        now = now or time.time()
        robot_id = self.last_clicked if self.last_clicked in self.states else "r2d2"
        state = self.states[robot_id]
        cutoff = now - 20 * 60
        history = self.activity_history.setdefault(robot_id, [])
        history[:] = [stamp for stamp in history if stamp >= cutoff]

        # Twenty bars represent twenty rolling one-minute buckets. The newest
        # minute is on the right, so the chart naturally moves with time.
        buckets = [0] * 20
        for stamp in history:
            age = int((now - stamp) // 60)
            if 0 <= age < 20:
                buckets[19 - age] += 1

        canvas = self.activity_canvas
        canvas.delete("all")
        for y in (12, 32, 52):
            canvas.create_line(5, y, 177, y, fill="#123242")
        peak = max(buckets) if buckets else 0
        scale = max(1, peak)
        left, baseline, step, bar_width = 6.0, 57.0, 8.55, 6.0
        for index, count in enumerate(buckets):
            x1 = left + index * step
            x2 = x1 + bar_width
            height = 0 if count == 0 else max(3.0, 43.0 * count / scale)
            canvas.create_rectangle(
                x1, baseline-height, x2, baseline,
                fill=state.spec.accent if count else LINE,
                outline="")
        canvas.create_text(5, 69, text="−20m", fill=MUTED, anchor="w",
                           font=("Courier", 6))
        canvas.create_text(177, 69, text="NOW", fill=state.spec.accent, anchor="e",
                           font=("Courier", 6, "bold"))
        canvas.create_text(91, 7, text=f"PEAK {peak}/min", fill=MUTED,
                           font=("Courier", 6))
        self.activity_label.configure(
            text=f"{state.spec.name} / LAST 20 MIN / {len(history)} EVENTS",
            fg=state.spec.accent)

    def _poll_orientation_if_due(self) -> None:
        now = time.monotonic()
        if now < self.next_orientation_poll:
            return
        self.next_orientation_poll = now + 1.0
        robot_id = self.last_clicked
        state = self.states.get(robot_id)
        if (not state or not state.connected or state.connection_stale or
                robot_id in self.orientation_polling):
            self._draw_radar_focus()
            return
        self.orientation_polling.add(robot_id)
        threading.Thread(target=self._orientation_worker, args=(robot_id,), daemon=True).start()

    def _orientation_worker(self, robot_id: str) -> None:
        try:
            sample = self.backend.get_orientation(robot_id)
            error = ""
        except Exception as exc:
            sample = {}
            error = exception_text(exc)
        if not self.closing:
            self.after(0, lambda rid=robot_id, data=sample, err=error:
                       self._finish_orientation(rid, data, err))

    def _finish_orientation(self, robot_id: str,
                            sample: Dict[str, Optional[float]], error: str) -> None:
        self.orientation_polling.discard(robot_id)
        if error:
            previous = self.orientation_errors.get(robot_id)
            self.orientation_errors[robot_id] = error
            if error != previous:
                self._log_error("ORIENTATION_READ", error, robot_id,
                                sensor="attitude/heading")
            self._mark_connection_stale(robot_id, error, "ORIENTATION_READ")
        else:
            self.orientation_data[robot_id] = sample
            self.orientation_errors.pop(robot_id, None)
            self.states[robot_id].connection_failures = 0
        if robot_id == self.last_clicked:
            self._draw_radar_focus()

    def _poll_batteries_if_due(self) -> None:
        now = time.monotonic()
        for robot_id, state in self.states.items():
            if (not state.connected or state.connection_stale or
                    robot_id in self.battery_polling or
                    now < self.next_battery_poll.get(robot_id, 0.0)):
                continue
            self.next_battery_poll[robot_id] = now + 60.0
            self.battery_polling.add(robot_id)
            threading.Thread(target=self._battery_worker,
                             args=(robot_id,), daemon=True).start()

    def _battery_worker(self, robot_id: str) -> None:
        try:
            sample = self.backend.get_battery(robot_id)
            error = ""
        except Exception as exc:
            sample = {}
            error = exception_text(exc)
        if not self.closing:
            self.after(0, lambda rid=robot_id, data=sample, err=error:
                       self._finish_battery_read(rid, data, err))

    def _finish_battery_read(self, robot_id: str,
                             sample: Dict[str, object], error: str) -> None:
        self.battery_polling.discard(robot_id)
        state = self.states[robot_id]
        if not state.connected:
            return
        if error:
            previous = self.battery_errors.get(robot_id)
            self.battery_errors[robot_id] = error
            if error != previous:
                self._log_error("BATTERY_READ", error, robot_id,
                                sensor="toy power commands")
            self._mark_connection_stale(robot_id, error, "BATTERY_READ")
        else:
            state.connection_failures = 0
            previous_percent = state.battery
            percent = sample.get("percent")
            voltage = sample.get("voltage")
            state.battery = int(percent) if percent is not None else None
            state.battery_voltage = float(voltage) if voltage is not None else None
            state.battery_state = (str(sample.get("power_state"))
                                   if sample.get("power_state") else None)
            self.battery_errors.pop(robot_id, None)
            if previous_percent is None:
                level = (state.battery_state or
                         ("LOW" if state.battery is not None and state.battery <= 20 else "OK"))
                voltage_text = (f"{state.battery_voltage:.2f} V"
                                if state.battery_voltage is not None else "voltage unavailable")
                percent_text = (f"estimated {state.battery}%"
                                if state.battery is not None else "percentage unavailable")
                self.log("POWER", f"{state.spec.name} / {voltage_text} / "
                         f"{percent_text} / {level}",
                         "warn" if level == "LOW" else "ok")
        self._update_telemetry()

    def _connect_robot(self, robot_id: str) -> None:
        if self.closing:
            return
        state = self.states[robot_id]
        if state.connected or state.connecting:
            return
        state.connecting = True
        self.log("BT LINK", f"Connecting to {state.spec.name}…")
        self._refresh_robot_ui()
        threading.Thread(target=self._connect_worker, args=(robot_id,), daemon=True).start()

    def _connect_worker(self, robot_id: str) -> None:
        error = ""
        try:
            result = self.backend.connect(robot_id)
        except Exception as exc:
            result = False
            error = exception_text(exc)
        if not self.closing:
            self.after(0, lambda rid=robot_id, ok=result, e=error: self._finish_connection(rid, ok, e))

    def _finish_connection(self, robot_id: str, success: bool, error: str = "") -> None:
        state = self.states[robot_id]
        state.connecting = False
        if not success:
            failure = error or "backend returned False"
            self._log_error("CONNECT", failure, robot_id,
                            discovered=state.discovered)
            retryable = (self._is_transport_failure(failure)
                         or failure == "backend returned False"
                         or "no matching unassigned device" in failure.lower()
                         or "unassigned ble device" in failure.lower())
            if (not self.closing and retryable and
                    self.connect_retry_counts[robot_id] < AUTO_CONNECT_RETRY_LIMIT):
                self.connect_retry_counts[robot_id] += 1
                attempt = self.connect_retry_counts[robot_id]
                try:
                    # The cached scanner object may still identify a device
                    # whose GATT session has already expired. Force the next
                    # connect() call to obtain a new BLE object.
                    self.backend.forget(robot_id)
                except Exception as exc:
                    self._log_error("CONNECT_FORGET", exception_text(exc), robot_id,
                                    retry_attempt=attempt)
                delay_ms = 1500 * attempt
                self.log("BT RETRY", f"{state.spec.name} / fresh scan in "
                         f"{delay_ms / 1000:.1f} s / automatic attempt "
                         f"{attempt}/{AUTO_CONNECT_RETRY_LIMIT}", "warn")
                job = self.after(delay_ms, lambda rid=robot_id: self._connect_robot(rid))
                self.after_jobs.add(job)
            elif not self.closing and retryable:
                self.log("BT OFFLINE", f"{state.spec.name} / automatic connection attempts "
                         "exhausted / click tile to retry", "error")
            elif not self.closing:
                self.log("BT OFFLINE", f"{state.spec.name} / connection failed / "
                         "click tile to retry", "error")
            self._refresh_robot_ui()
            return
        self.connect_retry_counts[robot_id] = 0
        state.discovered = True
        state.connected = True
        state.connection_stale = False
        state.connection_fault = ""
        state.connection_failures = 0
        self.battery_errors.pop(robot_id, None)
        self.orientation_errors.pop(robot_id, None)
        if robot_id in self.connection_order:
            self.connection_order.remove(robot_id)
        self.connection_order.append(robot_id)
        self._show_robot_tile(robot_id)
        # First launch: select automatically. Later launches: restore user selection.
        state.selected = state.spec.robot_id in self.preferred_selected if self.has_saved_selection else True
        if state.selected:
            self.preferred_selected.add(state.spec.robot_id)
        state.battery = None
        state.battery_voltage = None
        state.battery_state = None
        self.next_battery_poll[robot_id] = 0.0
        state.signal = random.randint(82, 99) if self.backend.is_simulation else 100
        identity = self.backend.identity(robot_id)
        self.log("CONNECTED", f"{state.spec.name} / {identity} / handshake OK", "ok")
        if state.spec.model_id == "bb9e" and not self.backend.is_simulation:
            self.log("DRIVE READY", f"{state.spec.name} / FULL CONTROL SYSTEM stabilization acknowledged",
                     "ok")
        self._refresh_robot_ui()
        self._save_settings()
        threading.Thread(target=self._restore_leds_worker, args=(robot_id,), daemon=True).start()
        job = self.after(280, lambda: self._confirmation_move(robot_id, initial=True))
        self.after_jobs.add(job)

    def _restore_leds_worker(self, robot_id: str) -> None:
        for channel, enabled in self.led_states.get(robot_id, {}).items():
            if (not enabled or self.closing or
                    self.states[robot_id].connection_stale):
                continue
            try:
                self.backend.set_led(robot_id, channel, True)
            except Exception as exc:
                if not self.closing:
                    error = exception_text(exc)
                    self.after(0, lambda rid=robot_id, ch=channel, e=error:
                               self._log_error("LED_RESTORE", e, rid, channel=ch,
                                               requested_state="ON"))

    def _confirmation_move(self, robot_id: str, initial=False) -> None:
        state = self.states[robot_id]
        if self.closing or not state.connected or state.connection_stale:
            return
        if robot_id in self.animation_busy:
            # A user command has priority over the periodic confirmation move.
            state.timer_job = self.after(
                5000, lambda rid=robot_id, first=initial: self._confirmation_move(rid, first))
            self.after_jobs.add(state.timer_job)
            return
        state.timer_job = None
        threading.Thread(target=self._confirmation_worker, args=(robot_id, initial), daemon=True).start()

    def _confirmation_worker(self, robot_id: str, initial: bool) -> None:
        error = ""
        action = ""
        try:
            confirmation = getattr(self.backend, "play_connection_confirmation", None)
            if callable(confirmation):
                action = confirmation(robot_id)
                ok = bool(action)
            else:
                ok = self.backend.move_head(robot_id)
        except Exception as exc:
            ok = False
            error = exception_text(exc)
        if not self.closing:
            self.after(0, lambda rid=robot_id, success=ok, first=initial,
                       e=error, description=action:
                       self._finish_confirmation(rid, success, first, e, description))

    def _finish_confirmation(self, robot_id: str, ok: bool, initial: bool,
                             error: str = "", action: str = "") -> None:
        state = self.states[robot_id]
        if ok:
            description = action or ("dome movement" if state.spec.model_id in
                                     ("r2d2", "r2q5") else "short turn")
            self.log("ACTION", f"{state.spec.name} / {description} — connection confirmation",
                     "ok")
            self._record_activity(robot_id)
            if self.backend.is_simulation:
                state.signal = max(60, min(100, state.signal + random.randint(-3, 2)))
            self._update_telemetry()
            if self.auto_confirm.get():
                self._schedule_confirmation(robot_id)
        else:
            reason = error or "no response during confirmation"
            if initial:
                self._log_error("CONNECTION_CONFIRMATION", reason, robot_id,
                                initial_test=True)
            self.disconnect_robot(robot_id, reason)

    def _schedule_confirmation(self, robot_id: str) -> None:
        state = self.states[robot_id]
        if state.timer_job:
            try:
                self.after_cancel(state.timer_job)
            except tk.TclError:
                pass
        low = max(1, min(15, min(self.interval_min.get(), self.interval_max.get())))
        high = min(15, max(self.interval_min.get(), self.interval_max.get()))
        delay_ms = random.randint(low * 60_000, high * 60_000)
        state.next_confirmation = time.time() + delay_ms / 1000
        state.timer_job = self.after(delay_ms, lambda: self._confirmation_move(robot_id))
        self.after_jobs.add(state.timer_job)

    def disconnect_robot(self, robot_id: str, reason="disconnected") -> None:
        state = self.states[robot_id]
        restore_job = self.led_restore_jobs.pop(robot_id, None)
        if restore_job:
            try:
                self.after_cancel(restore_job)
            except tk.TclError:
                pass
        if state.timer_job:
            try:
                self.after_cancel(state.timer_job)
            except tk.TclError:
                pass
        state.timer_job = None
        state.next_confirmation = None
        state.connecting = False
        state.connected = False
        state.connection_stale = False
        state.connection_fault = ""
        state.connection_failures = 0
        self.connect_retry_counts[robot_id] = 0
        if robot_id in self.connection_order:
            self.connection_order.remove(robot_id)
        state.selected = False  # Required: selection disappears on disconnect.
        state.battery = None
        state.battery_voltage = None
        state.battery_state = None
        self.battery_errors.pop(robot_id, None)
        self.preferred_selected.discard(robot_id)
        disconnect_error = ""
        forget_error = ""
        try:
            self.backend.disconnect(robot_id)
        except Exception as exc:
            disconnect_error = exception_text(exc)
        try:
            self.backend.forget(robot_id)
        except Exception as exc:
            forget_error = exception_text(exc)
        if disconnect_error:
            self._log_error("DISCONNECT", disconnect_error, robot_id, reason=reason)
        if forget_error:
            self._log_error("FORGET_SESSION", forget_error, robot_id, reason=reason)
        self.log("DISCONNECTED", f"{state.spec.name} / {reason}", "warn")
        self._refresh_robot_ui()
        self._save_settings()

    def force_disconnect(self, robot_id: str) -> None:
        state = self.states[robot_id]
        if not state.connected or robot_id in self.disconnect_busy:
            return
        self.disconnect_busy.add(robot_id)
        self.log("BT RESET", f"{state.spec.name} / forced disconnect requested", "warn")
        self._refresh_led_ui()
        threading.Thread(target=self._force_disconnect_worker, args=(robot_id,), daemon=True).start()

    def _force_disconnect_worker(self, robot_id: str) -> None:
        errors = []
        try:
            self.backend.stop_all(robot_id)
        except Exception as exc:
            errors.append(exception_text(exc))
        try:
            self.backend.disconnect(robot_id)
        except Exception as exc:
            errors.append(exception_text(exc))
        try:
            self.backend.forget(robot_id)
        except Exception as exc:
            errors.append(exception_text(exc))
        if not self.closing:
            self.after(0, lambda rid=robot_id, details="; ".join(filter(None, errors)):
                       self._finish_force_disconnect(rid, details))

    def _finish_force_disconnect(self, robot_id: str, error: str,
                                 reconnect: bool = False) -> None:
        state = self.states[robot_id]
        self.disconnect_busy.discard(robot_id)
        restore_job = self.led_restore_jobs.pop(robot_id, None)
        if restore_job:
            try:
                self.after_cancel(restore_job)
            except tk.TclError:
                pass
        if state.timer_job:
            try:
                self.after_cancel(state.timer_job)
            except tk.TclError:
                pass
        state.timer_job = None
        state.next_confirmation = None
        state.connecting = False
        state.connected = False
        state.connection_stale = False
        state.connection_fault = ""
        state.connection_failures = 0
        self.connect_retry_counts[robot_id] = 0
        if robot_id in self.connection_order:
            self.connection_order.remove(robot_id)
        state.selected = False
        state.battery = None
        state.battery_voltage = None
        state.battery_state = None
        self.battery_errors.pop(robot_id, None)
        self.preferred_selected.discard(robot_id)
        self.led_busy = {key for key in self.led_busy if key[0] != robot_id}
        self.animation_busy.discard(robot_id)
        suffix = f" / cleanup notes: {error}" if error else ""
        self.log("DISCONNECTED", f"{state.spec.name} / forced offline{suffix}", "warn")
        if error:
            self._log_error("FORCE_DISCONNECT_CLEANUP", error, robot_id)
        self._refresh_robot_ui()
        self._save_settings()
        if reconnect and not self.closing:
            if error:
                self.log("BT RECOVERY", f"{state.spec.name} / cleanup returned notes; "
                         "starting a fresh scan anyway", "warn")
            job = self.after(250, lambda rid=robot_id: self._connect_robot(rid))
            self.after_jobs.add(job)

    def execute_gesture(self, gesture_id: str) -> None:
        selected = [state for state in self.states.values()
                    if state.connected and not state.connection_stale and state.selected]
        if not selected:
            fallback = self.states.get(self.last_clicked)
            selected = ([fallback] if fallback and fallback.connected
                        and not fallback.connection_stale else [])
        compatible = [state for state in selected if gesture_id in state.animations]
        if not compatible:
            self.log("WARN", "No compatible connected robot for the selected animation", "warn")
            return
        ready = [state for state in compatible if state.spec.robot_id not in self.animation_busy]
        busy = [state.spec.name for state in compatible if state.spec.robot_id in self.animation_busy]
        if busy:
            self.log("BUSY", f"Animation already in progress / skipped: {', '.join(busy)}", "warn")
        compatible = ready
        if not compatible:
            return
        gesture_name = gesture_id
        names = ", ".join(state.spec.name for state in compatible)
        self.log("COMMAND", f"{gesture_name} → {names} / sending…")
        for state in compatible:
            self.animation_busy.add(state.spec.robot_id)
            threading.Thread(target=self._gesture_worker,
                             args=(state.spec.robot_id, gesture_id, gesture_name), daemon=True).start()

    def _gesture_worker(self, robot_id: str, gesture_id: str, gesture_name: str) -> None:
        try:
            ok = self.backend.execute_animation(robot_id, gesture_id)
            error = "" if ok else "command rejected"
        except Exception as exc:
            ok = False
            error = exception_text(exc)
        if not self.closing:
            self.after(0, lambda rid=robot_id, success=ok, e=error, name=gesture_name:
                       self._finish_animation(rid, name, success, e))

    def _finish_animation(self, robot_id: str, animation_name: str,
                          success: bool, error: str) -> None:
        state = self.states[robot_id]
        self.animation_busy.discard(robot_id)
        if success:
            state.connection_failures = 0
            self.log("ACK", f"{state.spec.name} / {animation_name} / firmware accepted command; "
                     "physical movement not sensor-verified", "ok")
            if state.spec.model_id == "bb9e":
                self.next_battery_poll[robot_id] = 0.0
            self._record_activity(robot_id)
            self._schedule_led_restore(robot_id)
        else:
            self._log_error("NATIVE_ANIMATION", error, robot_id,
                            animation=animation_name,
                            catalog_match=animation_name in state.animations)
            self._mark_connection_stale(robot_id, error, "NATIVE_ANIMATION")
            if "not connected" in error.lower():
                self.disconnect_robot(robot_id, error)

    def _schedule_led_restore(self, robot_id: str) -> None:
        previous = self.led_restore_jobs.pop(robot_id, None)
        if previous:
            try:
                self.after_cancel(previous)
            except tk.TclError:
                pass
        # Let movement/light effects finish before restoring persistent user LEDs.
        job = self.after(7000, lambda rid=robot_id: self._run_delayed_led_restore(rid))
        self.led_restore_jobs[robot_id] = job
        self.after_jobs.add(job)

    def _run_delayed_led_restore(self, robot_id: str) -> None:
        self.led_restore_jobs.pop(robot_id, None)
        if (self.closing or not self.states[robot_id].connected or
                self.states[robot_id].connection_stale):
            return
        threading.Thread(target=self._restore_leds_worker, args=(robot_id,), daemon=True).start()

    def _finish_gesture(self, robot_id: str, gesture_name: str, success: bool, error: str) -> None:
        state = self.states[robot_id]
        if success:
            state.connection_failures = 0
            suffix = (" / API completed; physical movement not sensor-verified"
                      if state.spec.model_id == "bb9e" else "")
            self.log("DONE", f"{state.spec.name} / {gesture_name}{suffix}", "ok")
            if state.spec.model_id == "bb9e":
                self.next_battery_poll[robot_id] = 0.0
            self._record_activity(robot_id)
        else:
            self._log_error("STANDARD_MOVEMENT", error, robot_id, command=gesture_name)
            self._mark_connection_stale(robot_id, error, "STANDARD_MOVEMENT")
            if "not connected" in error.lower():
                self.disconnect_robot(robot_id, error)

    def execute_standard(self, command_id: str) -> None:
        command = next((item for item in STANDARD_COMMANDS if item[0] == command_id), None)
        if command is None:
            self._log_error("STANDARD_LOOKUP", "command is absent from STANDARD_COMMANDS",
                            command_id=command_id)
            return
        _key, title, _description, _icon, supported_models = command
        selected = [state for state in self.states.values()
                    if state.connected and not state.connection_stale and state.selected]
        if not selected:
            fallback = self.states.get(self.last_clicked)
            selected = ([fallback] if fallback and fallback.connected
                        and not fallback.connection_stale else [])
        compatible = [state for state in selected if state.spec.model_id in supported_models]
        if not compatible:
            self.log("WARN", "No compatible connected robot for the selected movement", "warn")
            return
        names = ", ".join(state.spec.name for state in compatible)
        self.log("MANUAL", f"{title} → {names} / sending…")
        for state in compatible:
            robot_id = state.spec.robot_id
            work_queue = self.standard_queues[robot_id]
            waiting = work_queue.qsize() + (1 if robot_id in self.standard_busy else 0)
            work_queue.put((command_id, title))
            self._ensure_standard_worker(robot_id)
            if waiting:
                self.log("QUEUED", f"{state.spec.name} / {title} / "
                         f"position {waiting + 1}", "warn")

    def _ensure_standard_worker(self, robot_id: str) -> None:
        worker = self.standard_workers.get(robot_id)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(target=self._standard_robot_worker,
                                  args=(robot_id,), daemon=True)
        self.standard_workers[robot_id] = worker
        worker.start()

    def _standard_robot_worker(self, robot_id: str) -> None:
        work_queue = self.standard_queues[robot_id]
        while not self.closing:
            try:
                item = work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                return
            command_id, title = item
            state = self.states[robot_id]
            self.standard_busy.add(robot_id)
            if not state.connected or state.connection_stale:
                ok = False
                error = ("command skipped because BLE session is stale"
                         if state.connection_stale else "command skipped because robot is offline")
            else:
                try:
                    ok = self.backend.execute_standard(robot_id, command_id)
                    error = "" if ok else "command rejected"
                except Exception as exc:
                    ok = False
                    error = exception_text(exc)
            if not self.closing:
                finished = threading.Event()

                def finish_on_ui(rid=robot_id, success=ok, details=error,
                                 name=title, signal=finished):
                    try:
                        self._finish_gesture(rid, name, success, details)
                    finally:
                        signal.set()

                self.after(0, finish_on_ui)
                finished.wait(2.0)
            self.standard_busy.discard(robot_id)

    def toggle_led(self, robot_id: str, channel: str) -> None:
        state = self.states[robot_id]
        # Telemetry LED controls are intentionally direct controls. Group
        # selection (state.selected) is never consulted here.
        if not state.connected:
            self.log("WARN", f"{state.spec.name} / LED control requires a connection / "
                     "group checkbox is ignored", "warn")
            return
        if state.connection_stale:
            self.log("WARN", f"{state.spec.name} / LED control blocked by stale BLE session / "
                     "click robot tile to reconnect", "warn")
            return
        key = (robot_id, channel)
        if key in self.led_busy:
            return
        self.led_busy.add(key)
        previous = self.led_states[robot_id][channel]
        desired = not previous
        self.led_states[robot_id][channel] = desired
        self._refresh_led_ui()
        self._save_settings()
        threading.Thread(target=self._led_worker,
                         args=(robot_id, channel, desired, previous), daemon=True).start()

    def _led_worker(self, robot_id: str, channel: str, desired: bool, previous: bool) -> None:
        try:
            ok = self.backend.set_led(robot_id, channel, desired)
            error = "" if ok else "command rejected"
        except Exception as exc:
            ok = False
            error = exception_text(exc)
        if not self.closing:
            self.after(0, lambda: self._finish_led(robot_id, channel, desired, previous, ok, error))

    def _finish_led(self, robot_id: str, channel: str, desired: bool,
                    previous: bool, success: bool, error: str) -> None:
        state = self.states[robot_id]
        self.led_busy.discard((robot_id, channel))
        if success:
            state.connection_failures = 0
            status = "ON" if desired else "OFF"
            self.log("LED", f"{state.spec.name} / {channel.upper()} → {status}", "ok")
        else:
            self.led_states[robot_id][channel] = previous
            self._log_error("LED_COMMAND", error, robot_id, channel=channel,
                            requested_state="ON" if desired else "OFF",
                            restored_ui_state="ON" if previous else "OFF")
            self._mark_connection_stale(robot_id, error, "LED_COMMAND")
        self._refresh_led_ui()
        self._save_settings()

    def on_settings_changed(self) -> None:
        low = max(1, min(15, self.interval_min.get()))
        high = max(1, min(15, self.interval_max.get()))
        self.interval_min.set(min(low, high))
        self.interval_max.set(max(low, high))
        for state in self.states.values():
            if state.connected and not state.connection_stale:
                if self.auto_confirm.get():
                    self._schedule_confirmation(state.spec.robot_id)
                elif state.timer_job:
                    try:
                        self.after_cancel(state.timer_job)
                    except tk.TclError:
                        pass
                    state.timer_job = None
        self.log("CONFIG", "Automatic confirmation settings updated")
        self._save_settings()

    def on_always_on_top_changed(self) -> None:
        enabled = bool(self.always_on_top.get())
        self.attributes("-topmost", enabled)
        self.log("CONFIG", f"Keep app always on top / {'ON' if enabled else 'OFF'}")
        self._save_settings()

    def _refresh_robot_ui(self) -> None:
        self._refresh_robot_tile_order()
        for tile in self.robot_tiles.values():
            tile.draw()
        online = sum(state.connected and not state.connection_stale
                     for state in self.states.values())
        targets = sum(state.connected and not state.connection_stale and state.selected
                      for state in self.states.values())
        self.online_label.configure(text=f"●  {online} ONLINE")
        self.target_label.configure(text=f"{targets} TARGETS")
        self._refresh_gestures()
        self._refresh_led_ui()
        self._update_telemetry()
        self._refresh_telemetry_visibility()
        self._draw_radar_focus()
        self._refresh_macro_robot_menu()

    def _refresh_gestures(self) -> None:
        selected_id = self.last_clicked
        for gesture_id, button in self.gesture_buttons.items():
            supported_by = [s for s in self.states.values() if gesture_id in s.animations]
            enabled_for = [s for s in supported_by
                           if s.connected and not s.connection_stale]
            highlighted = bool(selected_id and any(s.spec.robot_id == selected_id for s in supported_by))
            button.update_state(supported_by, enabled_for, highlighted)
        command_map = {item[0]: item[4] for item in STANDARD_COMMANDS}
        for command_id, button in self.standard_buttons.items():
            supported_models = command_map[command_id]
            supported_by = [s for s in self.states.values() if s.spec.model_id in supported_models]
            enabled_for = [s for s in supported_by
                           if s.connected and not s.connection_stale]
            highlighted = any(s.spec.robot_id == selected_id for s in supported_by)
            button.update_state(supported_by, enabled_for, highlighted)

    def _refresh_led_ui(self) -> None:
        for (robot_id, channel), button in self.led_buttons.items():
            state = self.states[robot_id]
            # Deliberately independent of state.selected: telemetry controls
            # address their card's robot directly, even when its group checkbox
            # is OFF.
            enabled = (state.connected and not state.connection_stale
                       and (robot_id, channel) not in self.led_busy)
            button.update_state(self.led_states[robot_id][channel], enabled)
        for robot_id, button in self.disconnect_buttons.items():
            enabled = self.states[robot_id].connected and robot_id not in self.disconnect_busy
            button.update_state(False, enabled)

    def _update_telemetry(self) -> None:
        for robot_id, state in self.states.items():
            link_label = self.telemetry_link_labels[robot_id]
            id_label = self.telemetry_id_labels[robot_id]
            battery_label = self.telemetry_battery_labels[robot_id]
            confirm_label = self.telemetry_confirm_labels[robot_id]
            if state.connected and state.connection_stale:
                link_label.configure(text="BLE LINK  STALE / CLICK ROBOT TILE", fg=RED)
                id_label.configure(text=f"API ID    {robot_id.upper()} / SESSION UNRESPONSIVE",
                                   fg=RED)
                battery_label.configure(text="BATTERY   NO RESPONSE", fg=RED)
                confirm_label.configure(text="NEXT CONFIRM  SUSPENDED", fg=RED)
            elif state.connected:
                eta = "—"
                if state.next_confirmation:
                    eta = f"{max(0, int(state.next_confirmation - time.time())) // 60:02d}:{max(0, int(state.next_confirmation - time.time())) % 60:02d}"
                try:
                    details = self.backend.connection_details(robot_id)
                    connection_id = details.get("name", self.backend.connection_id(robot_id))
                    api_id = details.get("api_id", robot_id).upper()
                    bt_id = details.get("address", "—")
                except Exception:
                    connection_id = "CONNECTED DEVICE"
                    api_id = robot_id.upper()
                    bt_id = "UNAVAILABLE"
                link_label.configure(text=f"BLE LINK  {connection_id}", fg=GREEN)
                id_label.configure(text=f"API ID    {api_id} / BT ID {bt_id}", fg=state.spec.accent)
                if state.battery_voltage is not None:
                    percent = f"~{state.battery}%" if state.battery is not None else "—"
                    firmware_state = f" / {state.battery_state}" if state.battery_state else ""
                    battery_text = (f"BATTERY   {state.battery_voltage:.2f} V / "
                                    f"{percent}{firmware_state}")
                    if state.battery_state in ("LOW", "CRITICAL"):
                        battery_color = RED
                    elif state.battery_state == "CHARGING":
                        battery_color = CYAN
                    elif state.battery is not None and state.battery <= 20:
                        battery_color = RED
                    elif state.battery is not None and state.battery <= 40:
                        battery_color = AMBER
                    else:
                        battery_color = GREEN
                elif robot_id in self.battery_errors:
                    battery_text = "BATTERY   READ ERROR / SEE LOG"
                    battery_color = RED
                else:
                    battery_text = "BATTERY   READING…"
                    battery_color = AMBER
                battery_label.configure(text=battery_text, fg=battery_color)
                confirm_label.configure(text=f"NEXT CONFIRM  {eta}", fg=MUTED)
            else:
                link_label.configure(text="BLE LINK  OFFLINE", fg=RED)
                id_label.configure(text=f"API ID    {robot_id.upper()} / BT ID —", fg=MUTED)
                battery_label.configure(text="BATTERY   —", fg=MUTED)
                confirm_label.configure(text="NEXT CONFIRM  —", fg=MUTED)

    def _error_details(self, operation: str, error: str,
                       robot_id: Optional[str] = None, **context) -> str:
        details = [f"op={operation}", f"backend={self.backend.mode_label}"]
        if robot_id and robot_id in self.states:
            state = self.states[robot_id]
            try:
                device_identity = self.backend.identity(robot_id)
            except Exception:
                device_identity = "UNAVAILABLE"
            details.extend((
                f"robot={state.spec.name}",
                f"id={robot_id}",
                f"model={state.spec.robot_type}",
                f"device={device_identity}",
                f"connected={state.connected}",
                f"connecting={state.connecting}",
                f"connection_stale={state.connection_stale}",
                f"connection_failures={state.connection_failures}",
                f"selected={state.selected}",
                f"focused={state.focused}",
                f"battery_estimate={state.battery if state.battery is not None else 'unknown'}",
                f"battery_voltage={state.battery_voltage if state.battery_voltage is not None else 'unknown'}",
                f"battery_state={state.battery_state or 'unknown'}",
            ))
        for key, value in context.items():
            details.append(f"{key}={value}")
        details.append(f"detail={error or '<empty error>'}")
        return " | ".join(details)

    def _log_error(self, operation: str, error: str,
                   robot_id: Optional[str] = None, **context) -> None:
        self.log("ERROR", self._error_details(operation, error, robot_id, **context), "error")

    def log(self, event_type: str, message: str, style="type") -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_count += 1
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"{stamp}   {event_type:<13}{message}\n"
        file_error = self._append_log_file(line)
        connected_line = (event_type == "CONNECTED" and "handshake OK" in message)
        time_tag = "connected_line" if connected_line else "time"
        event_tag = "connected_line" if connected_line else style
        message_tag = "connected_line" if connected_line else None
        self.log_text.configure(state="normal")
        self.log_text.insert("end", stamp, time_tag)
        self.log_text.insert("end", f"   {event_type:<13}", event_tag)
        if message_tag:
            self.log_text.insert("end", f"{message}\n", message_tag)
        else:
            self.log_text.insert("end", f"{message}\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")
        if file_error:
            # File logging has already been disabled, so this diagnostic is
            # shown once in the UI without causing recursive write attempts.
            self.log("ERROR", self._error_details(
                "LOG_FILE_WRITE", file_error, path=LOG_PATH), "error")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        if self.log_file_enabled:
            try:
                LOG_PATH.write_text("", encoding="utf-8")
            except Exception as exc:
                self.log_file_enabled = False
                self.log_file_error = exception_text(exc)
                self.log("ERROR", self._error_details(
                    "LOG_FILE_CLEAR", self.log_file_error, path=LOG_PATH), "error")

    def _animate(self) -> None:
        if self.closing:
            return
        for tile in self.robot_tiles.values():
            tile.tick()
        self._poll_orientation_if_due()
        self._poll_batteries_if_due()
        now = time.monotonic()
        if now >= self.next_activity_redraw:
            self.next_activity_redraw = now + 5.0
            self._draw_activity_chart()
        self._update_telemetry()
        job = self.after(80, self._animate)
        self.after_jobs.add(job)

    def _update_clock(self) -> None:
        if self.closing:
            return
        now = time.monotonic()
        elapsed = now - self.last_clock_tick
        self.last_clock_tick = now
        # Fallback for Windows builds where a custom WndProc cannot be installed.
        # Tk timers pause during system sleep, so a long unexplained gap triggers
        # the same safe BLE reset and reconnect sequence.
        if (sys.platform == "win32" and elapsed > 20.0 and
                not self.power_suspended and not self.resume_in_progress):
            self._handle_power_resume(f"Tk timer gap {elapsed:.1f}s")
        self.clock_label.configure(text=datetime.now().strftime("%H:%M:%S"))
        job = self.after(1000, self._update_clock)
        self.after_jobs.add(job)

    # ── Safe shutdown ────────────────────────────────────────────────────────
    def request_close(self) -> None:
        if self.closing:
            return
        self._save_settings()
        self._save_macro_file()
        if self.macro_running:
            self.stop_macro()
        self.closing = True
        self.log("SHUTDOWN", "Stopping operations and closing BLE sessions…", "warn")
        if hasattr(self, "scan_label"):
            self.scan_label.configure(text="●  SHUTTING DOWN / BLE CLEANUP", fg=AMBER)
        self.update_idletasks()
        for state in self.states.values():
            if state.timer_job:
                try:
                    self.after_cancel(state.timer_job)
                except tk.TclError:
                    pass
                state.timer_job = None
        for job in tuple(self.after_jobs):
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        for work_queue in self.macro_queues.values():
            work_queue.put(None)
        for work_queue in self.standard_queues.values():
            work_queue.put(None)
        connected = [state for state in self.states.values()
                     if state.connected or state.connecting]
        self.shutdown_errors = []
        self.shutdown_workers = []
        for state in connected:
            worker = threading.Thread(
                target=self._shutdown_robot_worker,
                args=(state.spec.robot_id, state.connection_stale), daemon=True)
            self.shutdown_workers.append(worker)
            worker.start()
        self.shutdown_deadline = time.monotonic() + 8.0
        self.after(80, self._poll_shutdown_workers)

    def _shutdown_robot_worker(self, robot_id: str, stale: bool) -> None:
        """Attempt STOP then close one BLE session without blocking Tk."""
        if not stale:
            try:
                self.backend.stop_all(robot_id)
            except Exception as exc:
                self.shutdown_errors.append(("SHUTDOWN_STOP", robot_id,
                                             exception_text(exc)))
        try:
            reset = getattr(self.backend, "reset_connection", None)
            if callable(reset):
                reset(robot_id)
            else:
                self.backend.disconnect(robot_id)
                self.backend.forget(robot_id)
        except Exception as exc:
            self.shutdown_errors.append(("SHUTDOWN_DISCONNECT", robot_id,
                                         exception_text(exc)))

    def _poll_shutdown_workers(self) -> None:
        active = [worker for worker in self.shutdown_workers if worker.is_alive()]
        timed_out = bool(active and time.monotonic() >= self.shutdown_deadline)
        if active and not timed_out:
            self.after(80, self._poll_shutdown_workers)
            return
        try:
            finalize = getattr(self.backend, "finalize_shutdown", None)
            if callable(finalize):
                finalize()
        except Exception as exc:
            self.shutdown_errors.append(("BACKEND_FINALIZE", "", exception_text(exc)))
        for operation, robot_id, error in self.shutdown_errors:
            self._log_error(operation, error, robot_id or None)
        for state in self.states.values():
            state.connected = False
            state.connecting = False
            state.connection_stale = False
            state.selected = False
        if timed_out:
            names = [worker.name for worker in active]
            self.log("SHUTDOWN", f"BLE cleanup exceeded 8 s / forcing window close / "
                     f"pending_workers={len(names)}", "warn")
        else:
            self.log("SHUTDOWN", "All BLE sessions closed", "ok")
        self.update_idletasks()
        self._uninstall_power_event_hook()
        self.destroy()


def main() -> None:
    try:
        app = ControlDeck()
        app.mainloop()
    except tk.TclError as exc:
        print(f"Unable to start the Droids Control for Windows interface: {exc}")


if __name__ == "__main__":
    main()
