"""What the palette needs on top of the registry (wow/design.md §2.1): short category names,
instrument filters, pinyin, one-line usage texts and the ranked search (§2.4).

Everything here is keyed by registry id, so the palette never keeps a second list of
symbols: ``palette_symbols()`` is the registry minus hidden (low-confidence) symbols and
the program-managed ``volta.end``. Usage texts are catalog sources (N_); show with tr().
PINYIN was generated once with pypinyin (development only) and hand-corrected for
polyphones (彈 tán, 行 xíng); ``|`` separates the name from its parenthesised remark.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from . import symbols
from .i18n import N_, catalog, tr
from .text_words import TEXT_WORDS

CATEGORY_ORDER = ("fingering", "technique", "articulation", "dynamic", "line", "ornament", "barline",
                  "navigation", "tempo", "pedal")
CATEGORY_SHORT = {
    "fingering": N_("指／弓"), "technique": N_("技法"), "articulation": N_("演奏法"), "dynamic": N_("力度"),
    "line": N_("線條"), "ornament": N_("裝飾音"), "barline": N_("小節線"), "navigation": N_("跳躍"),
    "tempo": N_("速度"), "pedal": N_("踏板"),
}  # catalog sources; show tr(value)
INSTRUMENTS = ("erhu", "guzheng", "pipa", "dizi", "common")
MORE_INSTRUMENTS = ("ruan", "hulusi", "yangqin", "all")
ALL_GROUP = N_("全部")
DEFAULT_FAVORITES = ("finger.1", "finger.2", "finger.3", "bow.down", "bow.up", "erhu.vibrato", "slide.up",
                     "dyn.mf", "line.slur")
MAX_FAVORITES = 9
MAX_RECENTS = 8
NOT_IN_PALETTE = frozenset({"volta.end"})    # managed by the program when voltas are placed

# English words a musician may type that the catalogue keys do not already contain.
EXTRA_KEYS = {
    "slide.gliss": "slide glide portamento", "line.slur": "legato phrase", "line.tie": "tie hold",
    "art.staccato": "staccato dot", "erhu.vibrato": "vibrato", "tuplet.3": "triplet tuplet",
    "bar.final": "final end fine barline", "bar.double": "double barline", "nav.fine": "end",
    "art.fermata": "pause hold", "orn.trill": "tr", "finger.0": "open string",
}

PINYIN = {
    "dyn.pppp": "", "dyn.ppp": "", "dyn.pp": "", "dyn.p": "", "dyn.mp": "", "dyn.mf": "", "dyn.f": "", "dyn.ff": "", "dyn.fff": "", "dyn.ffff": "",
    "dyn.sf": "| tu qiang", "dyn.fz": "| tu qiang", "dyn.sfz": "| tu qiang", "dyn.sfzz": "| te qiang tu qiang",
    "dyn.fp": "| qiang hou ji ruo", "dyn.sfp": "| tu qiang hou ruo", "dyn.sfpp": "| tu qiang hou ji ruo",
    "dyn.rf": "| jia qiang", "dyn.rfz": "| jia qiang",
    "hairpin.cresc": "jian qiang", "hairpin.dim": "jian ruo", "hairpin.cresc_text": "jian qiang",
    "hairpin.dim_text": "jian ruo",
    "bar.dashed": "xu xiao jie xian", "bar.double": "shuang xiao jie xian", "bar.final": "zhong zhi xian",
    "bar.repeat_start": "fan fu qi", "bar.repeat_end": "fan fu zhi", "bar.repeat_both": "shuang xiang fan fu",
    "volta.1": "di yi jie wei | di yi fang", "volta.2": "di er jie wei | di er fang",
    "volta.3": "di san jie wei | di san fang", "volta.4": "di si jie wei | di si fang",
    "volta.final": "zui hou jie wei | jie wei fang", "volta.end": "fang zi jie shu",
    "nav.segno": "ji hao | tiao hui chu", "nav.tocoda": "| tiao dao wei sheng", "nav.ds": "| cong ji hao chu fan fu",
    "nav.dc": "| cong tou fan fu", "nav.fine": "| jie shu", "nav.coda_sign": "ji hao | jin fu hao",
    "nav.segno_sign": "| jin fu hao", "nav.dc_text": "| jin wen zi", "nav.dcfine_text": "| jin wen zi",
    "nav.ds_text": "| jin wen zi", "nav.dscoda_text": "| jin wen zi", "nav.fine_text": "| jin wen zi",
    "nav.tocoda_text": "| jin wen zi",
    "line.slur": "lian xian | yuan hua xian", "line.tie": "lian jie xian | yan yin xian", "tuplet.3": "san lian yin",
    "line.8va": "gao ba du", "line.8vb": "di ba du",
    "art.accent": "zhong yin", "art.marcato": "qiang zhong yin", "art.fermata": "yan chang ji hao",
    "art.tenuto": "bao chi yin", "art.staccato": "dun yin", "art.staccato_hollow": "ban dun yin",
    "art.staccatissimo": "xie xing duan yin | wu xian pu shi", "bow.up": "tui gong", "bow.down": "la gong",
    "tempo.accel": "jian kuai", "tempo.rit": "jian man", "tempo.atempo": "hui yuan su",
    "tempo.accel_span": "jian kuai | yan shen xian", "tempo.rit_span": "jian man | yan shen xian",
    "text.free": "wen zi | su du biao qing duan luo",
    "pedal.on": "ta ban cai xia", "pedal.off": "ta ban fang kai",
    "breath.comma": "huan qi | dou hao", "breath.v": "huan qi", "harmonic": "fan yin",
    "orn.prall": "shang bo yin", "orn.mordent": "xia bo yin", "orn.prallprall": "fu bo yin | chang bo yin",
    "orn.turn": "hui yin", "orn.trill": "chan yin",
    "orn.prall_sharp": "shang bo yin dai", "orn.prall_flat": "shang bo yin dai",
    "orn.prall_natural": "shang bo yin dai", "orn.mordent_sharp": "xia bo yin dai",
    "orn.mordent_flat": "xia bo yin dai", "orn.mordent_natural": "xia bo yin dai",
    "orn.trill_sharp": "chan yin dai", "orn.trill_flat": "chan yin dai", "orn.trill_natural": "chan yin dai",
    "orn.trill_span": "chan yin", "trem.1": "zhen yin | yi tiao", "trem.2": "zhen yin | er tiao",
    "trem.3": "zhen yin | san tiao", "trem.4": "zhen yin | si tiao",
    "arp.plain": "pa yin", "arp.up": "pa yin | xiang shang", "arp.down": "pa yin | xiang xia",
    "erhu.inner": "nei xian", "erhu.outer": "wai xian", "finger.0": "kong xian",
    "finger.1": "yi zhi", "finger.2": "er zhi", "finger.3": "san zhi", "finger.4": "si zhi", "finger.5": "wu zhi",
    "pos.1": "yi ba", "pos.2": "er ba", "pos.3": "san ba", "pos.4": "si ba", "pos.5": "wu ba",
    "slide.up": "shang hua yin", "slide.down": "xia hua yin", "slide.back": "hui hua yin",
    "slide.back_up": "shang hui hua yin", "slide.back_down": "xia hui hua yin",
    "slide.pad": "dian zhi hua yin", "erhu.vibrato": "rou xian", "erhu.press_vib": "ya rou",
    "erhu.slide_vib": "hua rou", "erhu.harm_art": "ren gong fan yin", "erhu.trem_bow": "chan gong",
    "erhu.dun": "dun gong", "erhu.pao": "pao gong", "erhu.tiao": "tiao gong", "erhu.liandun": "lian dun gong",
    "erhu.shift": "huan ba", "erhu.da": "da yin", "erhu.pizz_left": "zuo shou bo xian",
    "erhu.pizz_right": "you shou bo xian", "tune.up": "wei sheng", "tune.down": "wei jiang",
    "gz.tuo": "tuo", "gz.pi": "pi", "gz.mo": "mo", "gz.tiao": "tiao", "gz.gou": "gou", "gz.ti": "ti",
    "gz.tl": "ti", "gz.dacuo": "da cuo", "gz.xiaocuo": "xiao cuo", "gz.yao": "yao zhi", "gz.huazhi": "hua zhi",
    "gz.an": "an yin", "gz.chan": "chan yin | zuo shou an rou", "gz.gua_up": "gua zou | shang xing",
    "gz.gua_down": "gua zou | xia xing",
    "pipa.tan": "tan", "pipa.tiao": "tiao", "pipa.lun": "lun zhi", "pipa.sao": "sao", "pipa.fu": "fu",
    "pipa.yao": "yao", "pipa.gun": "gun", "pipa.tui": "tui", "pipa.la": "la", "pipa.yin": "yin",
    "pipa.rou": "rou", "pipa.daiqi": "dai qi",
    "dizi.tu1": "dan tu", "dizi.tu2": "shuang tu", "dizi.tu3": "san tu", "dizi.huashe": "hua she",
    "dizi.die": "die yin", "dizi.da": "da yin", "dizi.li_up": "li yin | shang", "dizi.li_down": "li yin | xia",
    "dizi.qizhen": "qi zhen yin", "dizi.zeng": "zeng yin",
    "bow.down_hook": "la gong | jing hu shi", "bow.up_hook": "tui gong | jing hu shi",
    "slide.gliss": "da hua yin | hua zou xian", "breath.v_bold": "huan qi | cu",
    "caesura": "ting dun ji hao", "orn.reverseturn": "ni hui yin", "tempo.sanban": "san ban",
    "dizi.k": "tu yin", "dizi.duo": "duo yin", "dizi.circular": "xun huan huan qi",
    "gz.shuangtuo": "shuang tuo", "stroke.lun": "lun zhi | liu qin ruan",
    "stroke.yao": "yao zhi | xie gan shuang qie xian", "ruan.lun3": "zhong ruan lun zhi | san lun",
    "ruan.lun4": "zhong ruan lun zhi | si lun", "hulusi.close": "guan bi fu guan",
    "hulusi.xuzhi": "xu zhi chan yin | quan bo lang", "line.wavy": "heng bo lang xian",
    "string.1": "quan yi | xian xu", "string.2": "quan er | xian xu", "string.3": "quan san | xian xu",
    "string.4": "quan si | xian xu", "string.5": "quan wu | xian xu", "yangqin.shuangtanlun": "shuang tan lun",
}

USAGE = {
    "dyn.pppp": N_("比 ppp 更弱，幾乎聽不見的音量。"),
    "dyn.ppp": N_("極弱，比 pp 更輕，像只剩氣息的聲音。"),
    "dyn.pp": N_("很弱，輕柔地演奏。"),
    "dyn.p": N_("弱，安靜柔和地演奏。"),
    "dyn.mp": N_("中弱，比 p 稍強，自然而含蓄。"),
    "dyn.mf": N_("中強，最常用的自然音量。"),
    "dyn.f": N_("強，飽滿有力地演奏。"),
    "dyn.ff": N_("很強，用全力但不破音。"),
    "dyn.fff": N_("極強，全曲最強的音量。"),
    "dyn.ffff": N_("比 fff 更強，極少使用的最強音量。"),
    "dyn.sf": N_("這個音突然加強，之後回到原來的力度。"),
    "dyn.fz": N_("這個音用力奏出，之後回到原來的力度。"),
    "dyn.sfz": N_("突強：只強調這一個音，之後音量不變。"),
    "dyn.sfzz": N_("特強的突強，比 sfz 更猛烈。"),
    "dyn.fp": N_("先強奏、立刻轉弱，後面保持弱。"),
    "dyn.sfp": N_("突強後馬上轉弱，接著弱奏。"),
    "dyn.sfpp": N_("突強後馬上轉為很弱。"),
    "dyn.rf": N_("加強：這個音或短句明顯加重。"),
    "dyn.rfz": N_("加強（rfz 寫法），效果同 rf。"),
    "hairpin.cresc": N_("從起點逐漸加強，到線尾的力度。"),
    "hairpin.dim": N_("從起點逐漸減弱，到線尾的力度。"),
    "hairpin.cresc_text": N_("用文字 cresc. 標示漸強，適合較長的段落。"),
    "hairpin.dim_text": N_("用文字 dim. 標示漸弱，適合較長的段落。"),
    "bar.dashed": N_("虛線小節線，用來細分長小節或散板。"),
    "bar.double": N_("段落分界：一個樂段結束時用的雙線。"),
    "bar.final": N_("樂曲結束時用的粗細雙線，放在這個小節的右邊。"),
    "bar.repeat_start": N_("反覆段落從這裡開始。"),
    "bar.repeat_end": N_("反覆段落到這裡，回到反覆起再奏一次。"),
    "bar.repeat_both": N_("前一段反覆結束，同時開始下一段反覆。"),
    "volta.1": N_("第一遍奏這個結尾，奏完回到反覆起。"),
    "volta.2": N_("第二遍跳過第一結尾，改奏這個結尾。"),
    "volta.3": N_("第三遍跳過前面的結尾，改奏這個結尾。"),
    "volta.4": N_("第四遍跳過前面的結尾，改奏這個結尾。"),
    "volta.final": N_("最後一個結尾標「結」，奏完即結束。"),
    "nav.segno": N_("標出 D.S. 要跳回的位置（Segno 記號）。"),
    "nav.tocoda": N_("第二遍奏到這裡，跳到尾聲（Coda）。"),
    "nav.ds": N_("奏到這裡，回到 Segno 記號處再奏（試聽回頭時反覆照奏）。"),
    "nav.dc": N_("奏到這裡，回到樂曲開頭再奏（試聽回頭時反覆照奏）。"),
    "nav.fine": N_("反覆回來時，在這裡結束全曲。"),
    "nav.coda_sign": N_("只印 Coda 記號，不改變試聽順序。"),
    "nav.segno_sign": N_("只印 Segno 記號，不改變試聽順序。"),
    "nav.dc_text": N_("只印文字 D.C.，試聽不會跳回。"),
    "nav.dcfine_text": N_("只印文字 D.C. al Fine，試聽不會跳回。"),
    "nav.ds_text": N_("只印文字 D.S.，試聽不會跳回。"),
    "nav.dscoda_text": N_("只印文字 D.S. al Coda，試聽不會跳回。"),
    "nav.fine_text": N_("只印文字 Fine，試聽不會在此結束。"),
    "nav.tocoda_text": N_("只印文字 To Coda，試聽不會跳躍。"),
    "line.slur": N_("把一串不同音高的音連成一句，一弓拉完（管樂一口氣吹完）。"),
    "line.tie": N_("連接兩個同音高的音，合成一個長音。"),
    "tuplet.3": N_("三個音平均佔用原本兩個同時值音符的時間。"),
    "line.8va": N_("線涵蓋的音都高八度演奏（簡譜通常用高低音點）。"),
    "line.8vb": N_("線涵蓋的音都低八度演奏（簡譜通常用高低音點）。"),
    "art.accent": N_("重音：這個音比前後的音奏得更重。"),
    "art.marcato": N_("強重音：比重音更強、更有力。"),
    "art.fermata": N_("延長：在這個音上自由延長停留。"),
    "art.tenuto": N_("保持音：把音值奏足，略微加重。"),
    "art.staccato": N_("頓音：奏得短促有彈性，音與音分開。"),
    "art.staccato_hollow": N_("半頓音：稍短而不斷，介於連與頓之間。"),
    "art.staccatissimo": N_("楔形斷音：五線譜的寫法，非常短促尖銳。"),
    "bow.up": N_("推弓：從弓尖往弓根推。"),
    "bow.down": N_("拉弓：從弓根往弓尖拉。"),
    "tempo.accel": N_("從這裡起逐漸加快。"),
    "tempo.rit": N_("從這裡起逐漸放慢。"),
    "tempo.atempo": N_("回到原來的速度。"),
    "tempo.accel_span": N_("漸快並畫出延伸虛線，標出漸快的範圍。"),
    "tempo.rit_span": N_("漸慢並畫出延伸虛線，標出漸慢的範圍。"),
    "text.free": N_("在音符上方加文字，如：中速、深情地、引子；印譜畫面都有。"),
    "pedal.on": N_("踩下延音踏板。"),
    "pedal.off": N_("放開延音踏板。"),
    "breath.comma": N_("在這裡換氣或稍作停頓。"),
    "breath.v": N_("換氣記號（V 形），笛子譜常用。"),
    "harmonic": N_("泛音：手指輕觸弦，奏出空靈的泛音。"),
    "orn.prall": N_("上波音：本音與上方音快速交替一次。"),
    "orn.mordent": N_("下波音：本音與下方音快速交替一次。"),
    "orn.prallprall": N_("複波音：與上方音快速交替兩次（或更多）。"),
    "orn.turn": N_("迴音：依序奏上方音、本音、下方音、本音。"),
    "orn.trill": N_("顫音：本音與上方音快速持續交替。"),
    "orn.prall_sharp": N_("上波音，上方的音要升高半音。"),
    "orn.prall_flat": N_("上波音，上方的音要降低半音。"),
    "orn.prall_natural": N_("上波音，上方的音用還原音。"),
    "orn.mordent_sharp": N_("下波音，下方的音要升高半音。"),
    "orn.mordent_flat": N_("下波音，下方的音要降低半音。"),
    "orn.mordent_natural": N_("下波音，下方的音用還原音。"),
    "orn.trill_sharp": N_("顫音，上方的音要升高半音。"),
    "orn.trill_flat": N_("顫音，上方的音要降低半音。"),
    "orn.trill_natural": N_("顫音，上方的音用還原音。"),
    "orn.trill_span": N_("顫音一直持續到波浪線結束。"),
    "trem.1": N_("一條斜線：分成八分音符反覆奏。"),
    "trem.2": N_("兩條斜線：分成十六分音符反覆奏。"),
    "trem.3": N_("三條斜線：快速反覆，即顫弓或輪奏。"),
    "trem.4": N_("四條斜線：極快速的反覆。"),
    "arp.plain": N_("琶音：把和弦快速分開依序奏出。"),
    "arp.up": N_("琶音由低音往高音依序奏出。"),
    "arp.down": N_("琶音由高音往低音依序奏出。"),
    "erhu.inner": N_("在內弦上演奏這個音。"),
    "erhu.outer": N_("在外弦上演奏這個音。"),
    "finger.0": N_("空弦：不按弦，直接拉空弦。"),
    "finger.1": N_("用食指（一指）按這個音。"),
    "finger.2": N_("用中指（二指）按這個音。"),
    "finger.3": N_("用無名指（三指）按這個音。"),
    "finger.4": N_("用小指（四指）按這個音。"),
    "finger.5": N_("第五指的指法標記，彈撥樂器常用。"),
    "pos.1": N_("一把：從這個音起用第一把位演奏。"),
    "pos.2": N_("二把：從這個音起用第二把位演奏。"),
    "pos.3": N_("三把：從這個音起用第三把位演奏。"),
    "pos.4": N_("四把：從這個音起用第四把位演奏。"),
    "pos.5": N_("五把：從這個音起用第五把位演奏。"),
    "slide.up": N_("從低處滑進這個音：手指由下往上滑到本音。"),
    "slide.down": N_("由本音往下滑：手指順弦向下滑出。"),
    "slide.back_up": N_("上回滑音：先往上滑，再回到本音。"),
    "slide.back_down": N_("下回滑音：先往下滑，再回到本音。"),
    "tune.up": N_("微升：比本音略高（不到半音），箭頭標在音上方。"),
    "tune.down": N_("微降：比本音略低（不到半音），箭頭標在音上方。"),
    "erhu.vibrato": N_("揉弦：手指在弦上揉動，使聲音波動。"),
    "erhu.trem_bow": N_("顫弓：用極快的短弓反覆拉奏。"),
    "erhu.dun": N_("頓弓：每弓拉出後稍停，音頭清楚有力。"),
    "erhu.pao": N_("拋弓：弓桿拋下，讓弓毛自然彈跳。"),
    "erhu.tiao": N_("跳弓：弓毛離弦跳動，奏出輕快短音。"),
    "erhu.liandun": N_("連頓弓：一弓內連續奏出幾個頓音。"),
    "erhu.pizz_left": N_("左手撥弦：用左手手指撥響琴弦。"),
    "erhu.pizz_right": N_("右手撥弦：用右手手指撥響琴弦。"),
    "gz.tuo": N_("托：大指向外彈弦。"),
    "gz.mo": N_("抹：食指向內彈弦。"),
    "gz.pi": N_("劈：大指向內彈弦。"),
    "gz.tiao": N_("挑：食指向外彈弦。"),
    "gz.gou": N_("勾：中指向內彈弦。"),
    "gz.ti": N_("剔：中指向外彈弦。"),
    "gz.gua_up": N_("刮奏：手指由低音往高音快速刮過琴弦。"),
    "gz.gua_down": N_("刮奏：手指由高音往低音快速刮過琴弦。"),
    "pipa.tan": N_("彈：右手食指向外彈弦。"),
    "pipa.tiao": N_("挑：右手大指向內挑弦。"),
    "pipa.lun": N_("輪指：右手手指依次輪流彈弦，聲音連綿。"),
    "pipa.tui": N_("推：左手把弦往內推，使音高上升。"),
    "pipa.la": N_("拉：左手把弦往外拉，使音高上升。"),
    "pipa.yin": N_("吟：左手按弦左右搖動，使音微微起伏。"),
    "pipa.rou": N_("揉：左手按弦揉動，使聲音波動。"),
    "dizi.tu1": N_("單吐：用舌尖吐出 T，每個音清楚分開。"),
    "dizi.tu2": N_("雙吐：T、K 交替吐音，奏快速音群。"),
    "dizi.tu3": N_("三吐：TTK 交替，適合三連音節奏。"),
    "dizi.huashe": N_("花舌：舌尖顫動，吹出滾動的聲音。"),
    "dizi.die": N_("疊音：本音前快速加上方的裝飾音。"),
    "dizi.da": N_("打音：手指快速拍打下方音孔，使音頭清楚。"),
    "dizi.zeng": N_("贈音：音尾加一個短促的上方音。"),
    "bow.down_hook": N_("京胡式拉弓記號（鉤形），京劇譜常用。"),
    "bow.up_hook": N_("京胡式推弓記號（鉤形），京劇譜常用。"),
    "slide.gliss": N_("從這個音滑到下一個音，畫一條斜線。"),
    "breath.v_bold": N_("粗體的換氣 V，提醒必須在此換氣。"),
    "caesura": N_("停頓記號：聲音完全中斷片刻再繼續。"),
    "orn.reverseturn": N_("逆迴音：依序奏下方音、本音、上方音、本音。"),
    "tempo.sanban": N_("散板：節拍自由，依演奏者處理。"),
    "dizi.k": N_("吐音 K：舌根發音，與 T 交替成雙吐。"),
    "dizi.duo": N_("剁音：用力吐音並迅速落下，聲音乾脆。"),
    "dizi.circular": N_("循環換氣：用鼻吸氣，聲音不中斷。"),
    "gz.shuangtuo": N_("雙托：大指連續托兩根弦。"),
    "stroke.lun": N_("輪指：手指依序快速彈弦，聲音連綿。"),
    "stroke.yao": N_("搖指：一指快速來回彈同一根弦。"),
    "ruan.lun3": N_("中阮三指輪：三指依序輪奏。"),
    "ruan.lun4": N_("中阮四指輪：四指依序輪奏。"),
    "hulusi.close": N_("關閉副管，只用主管發音。"),
    "hulusi.xuzhi": N_("虛指顫音：手指在孔上虛按顫動。"),
    "line.wavy": N_("波浪線：標出顫音或揉音持續的範圍。"),
    "string.1": N_("在第一弦上演奏（弦序）。"),
    "string.2": N_("在第二弦上演奏（弦序）。"),
    "string.3": N_("在第三弦上演奏（弦序）。"),
    "string.4": N_("在第四弦上演奏（弦序）。"),
    "string.5": N_("在第五弦上演奏（弦序）。"),
    "yangqin.shuangtanlun": N_("雙彈輪：兩支琴竹交替快速擊弦。"),
}
HIDDEN_USAGE = N_("待老師確認的印法；可以使用，但印出的樣子還沒有經過核對。")

# Marks that MIDI does not perform (wow/tech.md §3.3): only print.
_SILENT_PLAY = ("none", "as hairpin")


def in_palette(symbol) -> bool:
    return symbol.id not in NOT_IN_PALETTE


def palette_symbols(*, include_hidden: bool = False) -> list:
    return [s for s in symbols.symbols(include_hidden=include_hidden) if in_palette(s)]


def silent(symbol) -> bool:
    """Printed but not performed in the preview (design §3.4): 試聽不表現，只印出."""
    if symbol.kind == "bar":
        return symbol.print_only
    if symbol.id in ("line.tie", "tuplet.3", "line.8va", "line.8vb"):
        return False
    play = symbol.play.lower()
    return play.startswith("none") or symbol.category in ("tempo", "ornament", "technique") and "velocity" not in play


def usage(symbol_id: str) -> str:
    text = USAGE.get(symbol_id)
    if text is None:
        return tr(HIDDEN_USAGE)
    return tr(text)


def short_name(symbol) -> str:
    """Cell label: the name without its parenthesised remark (至多約 6 個全形字，其餘以 … 省略)."""
    name = symbol.display_name
    base = re.sub(r"（[^）]*）", "", name).strip()
    return base or name


def remark(symbol) -> str:
    match = re.search(r"（([^）]*)）", symbol.display_name)
    return match[1] if match else ""


def group_matches(symbol, instrument: str) -> bool:
    """Instrument filter: the instrument plus the common group; 通用 = only common; all = everything."""
    if instrument == "all":
        return True
    if instrument == "common":
        return symbol.group == "common"
    return symbol.group in ("common", instrument)


def count(instrument: str, *, include_hidden: bool = False) -> int:
    return sum(group_matches(s, instrument) for s in palette_symbols(include_hidden=include_hidden))


# ---------------------------------------------------------------- search (design §2.4)
_SYLLABLES = frozenset((
    "a ai an ang ao ba bai ban bang bao bei ben beng bi bian biang biao bie bin bing bo bu ca cai can cang cao ce "
    "cei cen ceng cha chai chan chang chao che chen cheng chi chong chou chu chua chuai chuan chuang chui chun "
    "chuo ci cong cou cu cuan cui cun cuo da dai dan dang dao de dei den deng di dia dian diao die ding diu dong "
    "dou du duan dui dun duo e ei en eng er fa fan fang fei fen feng fo fou fu ga gai gan gang gao ge gei gen geng "
    "gong gou gu gua guai guan guang gui gun guo ha hai han hang hao he hei hen heng hong hou hu hua huai huan "
    "huang hui hun huo ji jia jian jiang jiao jie jin jing jiong jiu ju juan jue jun ka kai kan kang kao ke kei "
    "ken keng kong kou ku kua kuai kuan kuang kui kun kuo la lai lan lang lao le lei leng li lia lian liang liao "
    "lie lin ling liu lo long lou lu luan lun luo lv lve ma mai man mang mao me mei men meng mi mian miao mie min "
    "ming miu mo mou mu na nai nan nang nao ne nei nen neng ni nian niang niao nie nin ning niu nong nou nu nuan "
    "nuo nv nve o ou pa pai pan pang pao pei pen peng pi pian piao pie pin ping po pou pu qi qia qian qiang qiao "
    "qie qin qing qiong qiu qu quan que qun ran rang rao re ren reng ri rong rou ru rua ruan rui run ruo sa sai san "
    "sang sao se sen seng sha shai shan shang shao she shei shen sheng shi shou shu shua shuai shuan shuang shui "
    "shun shuo si song sou su suan sui sun suo ta tai tan tang tao te teng ti tian tiao tie ting tong tou tu tuan "
    "tui tun tuo wa wai wan wang wei wen weng wo wu xi xia xian xiang xiao xie xin xing xiong xiu xu xuan xue xun "
    "ya yan yang yao ye yi yin ying yo yong you yu yuan yue yun za zai zan zang zao ze zei zen zeng zha zhai zhan "
    "zhang zhao zhe zhei zhen zheng zhi zhong zhou zhu zhua zhuai zhuan zhuang zhui zhun zhuo zi zong zou zu zuan "
    "zui zun zuo").split())
_MAX_SYLLABLE = max(map(len, _SYLLABLES))


def fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def segment_pinyin(text: str) -> tuple[list[str], str] | None:
    """Split latin input into whole syllables plus a possibly unfinished last one.

    ``huayin`` -> (["hua", "yin"], ""); ``huay`` -> (["hua"], "y"); spaces separate too.
    Returns None when the text is not pinyin at all.
    """
    words = [w for w in re.split(r"[\s']+", text) if w]
    if not words or any(not re.fullmatch(r"[a-zv]+", w) for w in words):
        return None
    whole: list[str] = []
    for index, word in enumerate(words):
        last = index == len(words) - 1

        @lru_cache(maxsize=None)
        def split(start: int) -> tuple | None:
            if start == len(word):
                return ((), "")
            best = None
            for size in range(min(_MAX_SYLLABLE, len(word) - start), 0, -1):
                piece = word[start:start + size]
                if piece in _SYLLABLES:
                    rest = split(start + size)
                    if rest is not None:
                        return ((piece, *rest[0]), rest[1])
            if last and any(s.startswith(word[start:]) for s in _SYLLABLES):
                best = ((), word[start:])
            return best

        result = split(0)
        if result is None:
            return None
        whole.extend(result[0])
        partial = result[1]
        if partial and not last:
            return None
    return whole, partial if words else ""


def _pinyin_parts(symbol_id: str) -> tuple[list[str], list[str]]:
    base, _, remark_text = PINYIN.get(symbol_id, "").partition("|")
    return base.split(), remark_text.split()


def _find(seq: list[str], query: list[str], partial: str) -> tuple[int, int] | None:
    """(kind, position): kind 0 = whole syllables, 1 = last syllable only typed partly."""
    need = len(query) + (1 if partial else 0)
    for start in range(0, len(seq) - need + 1):
        if seq[start:start + len(query)] == query:
            if not partial:
                return 0, start
            if seq[start + len(query)].startswith(partial):
                return 1, start
    return None


def _score(symbol, query: str, folded: str, pinyin) -> tuple | None:
    simplified = catalog().get(symbol.name, symbol.name)
    names = [fold(symbol.name), fold(simplified)]
    best = None

    def consider(rank):
        nonlocal best
        if best is None or rank < best:
            best = rank
    for name in names:
        base = re.sub(r"（[^）]*）", "", name).strip()
        if base == folded:
            consider((0, -1))
        elif base.startswith(folded):
            consider((0, 0))
        elif folded in base:
            consider((1, base.index(folded)))
        elif folded in name:
            consider((2, name.index(folded)))
    if pinyin is not None:
        whole, partial = pinyin
        base, remark_seq = _pinyin_parts(symbol.id)
        if whole or partial:
            for offset, seq in ((0, base), (10, remark_seq)):
                found = _find(seq, whole, partial) if whole else (
                    next(((1, i) for i, s in enumerate(seq) if s.startswith(partial)), None))
                if found:
                    kind, position = found
                    consider((3 + kind, offset + position))
    words = fold(" ".join((symbol.id.replace(".", " ").replace("_", " "), symbol.keys,
                           EXTRA_KEYS.get(symbol.id, "")))).split()
    if any(w.startswith(folded) for w in words) or symbol.id.casefold() == folded:
        consider((5, 0))
    return best


def search(query: str, *, instrument: str = "erhu", include_hidden: bool = False) -> list:
    """Ranked matches over every palette symbol (filters do not apply, design §2.4)."""
    folded = fold(query).strip()
    if not folded:
        return []
    pinyin = segment_pinyin(folded) if re.fullmatch(r"[a-zv\s']+", folded) else None
    ranked = []
    for symbol in palette_symbols(include_hidden=include_hidden):
        rank = _score(symbol, query, folded, pinyin)
        if rank is not None:
            preference = 0 if symbol.group == instrument else 1 if symbol.group == "common" else 2
            ranked.append((rank[0], preference, rank[1], symbols.ORDER[symbol.id], symbol))
    ranked.sort(key=lambda item: item[:4])
    return [item[-1] for item in ranked]


def hidden_matches(query: str, instrument: str = "erhu") -> list:
    visible = {s.id for s in search(query, instrument=instrument)}
    return [s for s in search(query, instrument=instrument, include_hidden=True) if s.id not in visible]


# ---------------------------------------------------------------- ready-made text words (round 6)
TEXT_PREFIX = "text.free:"
MAX_TEXT = 20


def text_suggestions(query: str, *, found: bool = True) -> list[str]:
    """Words to offer as 「加上文字「…」」 for a search: the ready-made words that match (in
    the script the user typed), and the query itself when nothing else matched."""
    folded = fold(query).strip()
    if not folded:
        return []
    out = []
    for tc, sc, keys in TEXT_WORDS:
        for word in (tc, sc):
            if folded in fold(word) and word not in out:
                out.append(word)
                break
        else:
            if (re.fullmatch(r"[a-z0-9\s']+", folded) and any(k.startswith(folded.replace(" ", ""))
                                                                for k in keys.split())) or folded in keys.split():
                out.append(tc)
    exact = query.strip()
    if not out and not found and 0 < len(exact) <= MAX_TEXT and not any(c in exact for c in "\\{}\"$#%"):
        out.append(exact)
    return out[:6]


def text_word(sid: str) -> str:
    """The word of a 「加上文字」 result id (``text.free:中速``), or ""."""
    return sid[len(TEXT_PREFIX):] if sid.startswith(TEXT_PREFIX) else ""
