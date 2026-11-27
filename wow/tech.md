# 技術規格：專案檔 v2、記號目錄、弱起、多聲部（0.4.0 第 2 輪）

範圍：plan.md 的 S3–S8 與 D1–D3。這是建造規格。每一條匯出對應都已經用真實工具鏈跑過：vendored jianpu-ly 1.891，以子程序方式執行，做法和 `worker.py` 一樣；LilyPond 2.26.0（macOS arm64）；PDF 用 PySide6 QtPdf 轉成 PNG 後人工逐張檢查；MIDI 用探針裡的 SMF 讀取器解析。探針、輸出與重現指令見 §6。日期 2026-11-27。

## 0. 先看這裡：探針找到的既有問題（v1 也受影響）

| # | 發現 | 證據 | 處理 |
|---|---|---|---|
| P1 | **LilyPond 2.26 印不出 jianpu-ly 的 `Fr=` 記號**：二胡指法 一二三四、宀、`staccato`→▼、`tilde`。這些記號是 `\finger \markup{"一"}`，而 Fingering 用音樂字型（fetaText），裡面沒有 CJK 和 U+25BC，2.26 也不會改用其他字型補字。log 只出現 `no glyph for character … in emmentaler-11.otf`，PDF 上就是空白。v1 的指法和頓音升到 2.26 後會**無聲消失**。 | sheet_erhu 前後對照 | prelude 加 `\layout { \context { \Voice \override Fingering.font-encoding = #'latin1 \override Fingering.font-size = #2 } }`。驗證：字元印出，大小和其他技法字一致。**平台線升 2.26 時必須一起帶上**（只改 worker） |
| P2 | jianpu-ly 的 `Fine` 寫成 `\volta 2 \fine \volta 1`，後面接其餘音樂但**沒有加大括號**，所以 `\volta 1` 只管到下一個音。結果是 D.C./D.S. al Fine 的 MIDI 會播過 Fine，LilyPond 也會警告 `found music after \fine`。 | dc_al_fine_nofix：26 音（整首播兩遍）；修正後 21 音＝正確 | worker 後處理 `fix_fine()`：把 `\volta 1` 之後、到所屬區塊結尾的內容包進 `{ }`（依大括號深度掃描，略過 `%{ %}`、字串、註解）。修正後沒有警告，MIDI 正確 |
| P3 | 上游 `R{` 會對前一個字執行 `re.sub(r' \\bar "|."$',"",…)`。這裡的 `|` 被當成正規表示式的「或」，所以**任何以 `"` 結尾的字放在 `R{` 前面，結尾兩個字元都會被吃掉**，例如 `\bar "||"` 會變成 `\bar "|`，直接造成語法錯誤。 | repeat_novolta_bar_lines ✗ | 匯出規則：`R{` 前面不可出現以 `"` 結尾的字。反覆起點的小節線優先，前一小節的線型直接捨棄並提示使用者。目錄裡的記號都是 `\jsX` 巨集，不受影響 |
| P4 | 一行的第一個字如果長得像 `名稱=`（`slide=⌒`、`Fr=…`），整行會被當成**標頭並被靜默丟掉**，不會出現任何錯誤。 | combo_slide_line_start：第 2 小節消失 | 匯出規則：這種行要接到上一行後面（`proto_export.py` 已實作）。validate 也要擋使用者原始碼裡的這種行 |
| P5 | 使用 `Fine` 但後面沒有 D.C./D.S. 時，PDF 上**不會印出「Fine」**，只會出現終止線。 | fine_alone | 匯出時改用只印不跳的 `\jsFineText` |
| P6 | LilyPond 預設不在曲首印出起始反覆線 `|:`，但簡譜慣例要印。 | repeat_volta | 反覆從第 0 小節開始時，在 `R{` 之後補 `\bar ".|:"`，放在 R{ 後面就不會觸發 P3 |

## 1. 專案檔 v2

### 1.1 JSON

```json
{"format": "jianpu-studio", "version": 2,
 "mode": "visual", "source": "",
 "score": {
   "title": "…", "composer": "…", "key": "D", "meter": "4/4", "tempo": 80,
   "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0,
   "pickup": 0,
   "print_variant": "tc",
   "parts": [
     {"name": "二胡一", "lyrics": "…",
      "notes": [ {"degree": 5, "octave": 0, "duration": 8, "dotted": false, "accidental": "",
                  "tie": false, "tuplet": 0, "tuplet_start": false, "tuplet_end": false,
                  "grace_before": "", "grace_after": "",
                  "marks": ["finger.1", "bow.down"],
                  "spans_start": ["line.slur"], "spans_end": [],
                  "bar_start": ["bar.repeat_start"], "bar_end": []} ]}
   ]}}
```
- 存檔時省略等於預設值的欄位，讀檔時補回預設值。fixtures 的 JSON 就是這個寫法。
- 舊版程式遇到 `version: 2` 時已經會拒開（`from_dict` 要求 1）。這件事 D2 已接受。

### 1.2 dataclass

```python
@dataclass(frozen=True)
class Note:                       # 音高與時值欄位和 v1 相同
    degree: int = 1; octave: int = 0; duration: int = 4; dotted: bool = False; accidental: str = ""
    tie: bool = False                                  # 結構欄位：要檢查前後同音高；MIDI 會合併成一個音
    tuplet: int = 0; tuplet_start: bool = False; tuplet_end: bool = False   # 結構欄位：tuplet_ranges()
    grace_before: str = ""; grace_after: str = ""      # 結構欄位：倚音字串，和 v1 相同
    marks: tuple[str, ...] = ()        # 點記號 id，不重複，依登記表順序排列
    spans_start: tuple[str, ...] = ()  # 從這個音開始的線條 id
    spans_end: tuple[str, ...] = ()    # 在這個音結束的線條 id（依 family 配對）
    bar_start: tuple[str, ...] = ()    # 只放在 parts[0]、某小節第一個音
    bar_end: tuple[str, ...] = ()      # 只放在 parts[0]、某小節最後一個音

@dataclass
class Part:
    name: str = ""; notes: list[Note] = field(default_factory=list); lyrics: str = ""

@dataclass
class Score:
    title, composer, key, meter, tempo, with_staff, paper_size, staff_size, bars_per_line  # 同 v1
    pickup: int = 0                 # 弱起長度（tick，48/四分音符）；0 表示沒有弱起
    print_variant: str = "tc"       # 譜面上技法字用繁體或簡體；新檔依介面語言決定，v1 轉檔一律 "tc"
    parts: list[Part]               # 1–4 個聲部（建議上限）
```
用 tuple，是因為 `Note` 是 frozen、可 hash，復原紀錄可以直接共用同一個音符物件，和 v1 一樣。登記表裡記號的 `kind` 決定它放在哪個欄位：`point`／`pre` 放 `marks`，`span` 放 `spans_*`，`bar` 放 `bar_*`。

### 1.3 小節層級的資料放哪裡、怎麼撐過編輯

- **只存在 `parts[0]`**。小節線樣式、反覆、房子、導航記號掛在主聲部某小節的第一個音（`bar_start`）或最後一個音（`bar_end`）。匯出時複製到其他聲部的同一個小節（見 §3）。其他聲部的 `bar_*` 一律是空的，validate 會檢查。
- 小節範圍仍然用 tick 算，但要加上弱起：第一條小節線在 `p = pickup or bar` 的位置，之後每 `bar` 一條。`measure_ranges` 的判斷式改成 `(pos - p) % bar == 0 and pos >= p`。
- **新增小節**：新的休止小節插在 `ranges[k][0]` 前面，原本掛在該小節第一個音上的 `bar_start` 跟著內容往後移。這是想要的結果：記號屬於那段音樂，不屬於小節編號。
- **刪除／清空小節**：被刪除的音符上的 `bar_*` 一起消失。成對的項目（`bar.repeat_start`↔`bar.repeat_end`、`volta.1`…`volta.end`、`nav.segno`↔`nav.ds`、`nav.tocoda`↔`nav.ds`）沿用 `_finish` 的 `_spans` 配對保留邏輯：只剩一半的那一端要移除，並把移除數量加進「已移除 N 個…」的提示。`_CONNECTIONS` 擴充成「所有 span 與成對的 bar 項目」。
- **複製小節**：完整複製到的成對項目保留，只複製到一半的移除。導航記號（Segno、ToCoda、DS、DC、Fine）在複製出來的那份裡**一律移除**，因為整首只能出現一次。
- **多聲部的小節編輯**：`insert/delete/clear/duplicate_measures` 必須對每個聲部都做同一個小節的操作，而且整批算一次復原步驟（一個 snapshot 包含所有聲部）。
- **複製貼上**：音符連同 `marks/spans/bar_*` 一起貼上，貼完跑一次 validate。只配到一半的 span 或 bar 項目依上一條規則移除並提示。
- **復原**：`Project.snapshot()` 要對每個 part 的 notes 做淺拷貝（`[replace(p, notes=list(p.notes)) for p in parts]`）。

### 1.4 v1 → v2 轉換（`Project.from_dict` 遇到 version 1 時執行）

| v1 欄位 | v2 | 規則 |
|---|---|---|
| degree/octave/duration/dotted/accidental | 不變 | |
| `finger` -1/0–4 | `marks += finger.N` | **0 的印法會改變**：v1 印 宀（上游 Fr=0 的寫法），v2 依 JP-Word 印粗體 0（`\jsFingerZero`）。要寫進版本說明 |
| `bow` upbow/downbow | `bow.up` / `bow.down` | |
| `dynamic` ppp…fff, sfz | `dyn.X` | |
| `articulation` | staccato→`art.staccato`，tenuto→`art.tenuto`，accent→`art.accent`，fermata→`art.fermata`，trill→`orn.trill`，mordent→`orn.mordent`，breathe→`breath.comma`，turn→`orn.turn`，marcato→`art.marcato`，staccatissimo→`art.staccatissimo`，harmonic→`harmonic` | |
| `slide` up/down | `slide.up` / `slide.down` | |
| `slur_start/slur_end` | `spans_start/spans_end += line.slur` | |
| `tie` | 保留 | |
| `repeat_start/repeat_end` | `bar_start += bar.repeat_start` / `bar_end += bar.repeat_end` | 舊檔只有一個聲部 |
| `tuplet*` | 保留 | |
| `hairpin` crescendo/diminuendo, `hairpin_end` | `spans_start += hairpin.cresc/dim`；遇到 `hairpin_end` 時，依目前未結束的 hairpin 種類加進 `spans_end` | 依序掃描 |
| `grace_before/after` | 保留 | |
| `Score.notes`, `Score.lyrics` | `parts=[Part("", notes, lyrics)]` | `pickup=0`，`print_variant="tc"` |

v1 轉成 v2 再匯出的結果，除了空弦 0 的印法，和 v1 的 `to_jianpu` 逐字相同。因為記號排序沿用登記表順序（指法最先），只有 `\bow`、`\dyn` 等記號的先後順序可能和 v1 不同；這些都是 post-event，LilyPond 的輸出一樣。

### 1.5 驗證規則（`Score.validate` 與 `notation_error`）

| 規則 | 說明 |
|---|---|
| 記號存在 | 每個 id 都要在登記表裡，而且 kind 和所在欄位相符 |
| 互斥群組（`slot`） | 每個音最多一個：dyn.*、finger.*、string.*、bow.*（含 hook）、slide.*（上游只有一個 pendingSlide）、arp.*、trem.*（含 `erhu.trem_bow`）、{art.staccato, staccato_hollow, staccatissimo}、{erhu.inner, erhu.outer}、ornament 群（orn.* 點記號）、breath 群（breath.*、caesura）、tempo 文字 |
| 休止符 | 只允許 `on_rest` 的記號：art.fermata、dyn.*、breath.*、caesura、tempo.*、pedal.*、nav 與 bar 項目。指法、弓法、滑音、裝飾音、震音、琶音、技法都不行（沿用 v1 精神） |
| 後倚音 | 沿用 v1：有 `grace_after` 的音只能再加 breath.* 記號，也不能放 span 端點 |
| 線條配對 | 同一 family 的起點和終點要交替出現，不可重疊。family 有：hairpin（cresc/dim 兩種形式共用）、slur（允許同一個音先結束再開始）、ottava、trill（trill_span/line.wavy/hulusi.xuzhi）、text（rit/accel span）。漸強弱線進行中不能出現新的 dyn（v1 規則） |
| 8va／8vb | 起點和終點必須在**同一個結構區塊**裡：中間不可跨過 `R{`、`}`、`A{`、volta 分隔、Segno、ToCoda、DS、DC、Fine。原因是 MIDI 改寫會插入 `\transpose {…}`（§3） |
| 小節邊界 | `bar_start` 只能放在某小節第一個音，`bar_end` 只能放在最後一個音，而且只能在 `parts[0]` |
| 反覆 | `bar.repeat_start` 和 `bar.repeat_end` 一對一配對，不可巢狀。`bar.repeat_end` 只放在反覆本體的結尾，**不放在房子結尾** |
| 房子 | `volta.1` 必須緊接在 `bar.repeat_end` 那個小節之後；`volta.k` 要連號；最後一房必須有 `volta.end`；`volta.final`（結）只能和最後一房並用；k 最多 4 |
| 導航 | 整首最多一個 DS 或 DC，不能兩者都有。Segno 要在 DS 之前。ToCoda 要在 Segno 和 DS 之間。Fine 要在跳躍點之前。ToCoda 和 Fine 不能同時存在。**導航項目不可放在反覆本體或房子內部**（tocoda_inside_repeat：LilyPond 警告 `More alternatives than repeats`）。最後一房結尾的 Fine 合法，匯出時放在 `}` 之後（dc_with_volta_fine_after ✓）。有 Fine 時跳躍記號須在最後一小節（後面的小節永遠不會演奏，LilyPond 卻會播放）；To Coda 不可和無房子的反覆終點共用小節線（上游會把它寫成該反覆的房子） |
| 弱起 | `pickup` 只能是單一時值或附點時值（1、2、4、8、16、32 分，可附點），而且 `0 < pickup < bar`。jianpu-ly 只支援 `4/4,8`、`3/4,4.` 這兩種寫法，例如 5 個十六分音符就不能當弱起 |
| 最後一小節 | 有弱起時，最後一小節長度必須是 `bar - pickup`（上游規則）或完整的 `bar`。後者匯出時 worker 要設定 `j2ly_sloppy_bars=1`（f03 ✓）。「補齊休止符」要補到下一個允許的長度 |
| 聲部 | 1–4 個聲部，**總 tick 數必須相等**，小節邊界也要一致（上游不檢查：parts_unequal 照樣編譯成功，所以只能由 app 擋）。每個聲部的 `pickup` 共用 |
| 文字 | `Part.name` 用 `text_value(…, 40)`，`lyrics` 用 `text_value(…, 20000)`，匯出時套用 `literal()` |

## 2. 記號目錄

Python 登記表草稿：`work/probes/notation/catalog.py`（`S` 串列，每筆有 id、cat、group、tw、cn、keys、kind、place、canvas、export、ly、play、conf、note）。巨集定義由 `prelude.py` 從登記表產生。下表由 `gen_tables.py` 從同一份資料產生，不是手寫的。

- **canvas** 欄：`text:` 表示在對應的 lane 畫出那些字（繁／簡用 `/` 分開）；`shape:` 表示畫布要自己畫的形狀。畫布的 lane 順序，從音符往外：指法／弦序 → 演奏法符號（頓音、重音、裝飾音）→ 弓法 → 內外弦與技法文字 → 速度文字。下方依序是：震音斜線、力度、漸強弱、踏板。
- **export** 欄（jianpu-ly 的字）：`post:` 放在音頭之後、延長線之前；`pre:` 放在音符之前；`after:` 放在延長線與延音線之後；`tok:` 直接改音符字本身；`span:起點|終點`；`bar:start|end:` 是小節層級。表中的 `¦` 代表 `|`。
- **probe** 欄：`✓ seen` 表示 LilyPond 2.26 rc=0、沒有警告，而且我看過 PNG 確認記號真的出現；`[case]` 是對應的探針輸出資料夾。
- **conf** 欄：印刷形式符合中文常見慣例的信心。**低信心的列一律標成「DEFERRED: teacher confirmation」：技術上已驗證可以印，但 0.4.0 面板預設不顯示**，等二胡、古箏、琵琶、笛子老師核對後再開放。
- **JP-Word** 欄：對照 wow/research/jpword-symbols.md，yes／partial／—（— 表示 JP-Word 沒有可對應的已命名符號）。我們的畫法全部自己繪製（文字字元、LilyPond 內建字形或 \postscript），**不依賴 JP-Word 的字型或碼位**。

### 2.1 目錄表
| id | cat | group | zh_TW | zh_CN | search keys | kind | place | canvas | export (jianpu-ly words) | playback | probe | conf | JP-Word |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `dyn.ppp` | dynamic | common | ppp | ppp | ppp lidu | point | below | text:ppp | `post:\ppp` | channel volume level (absolute-volume-alist) | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.pp` | dynamic | common | pp | pp | pp lidu | point | below | text:pp | `post:\pp` | channel volume level (absolute-volume-alist) | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.p` | dynamic | common | p | p | p lidu | point | below | text:p | `post:\p` | channel volume level (absolute-volume-alist) | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.mp` | dynamic | common | mp | mp | mp lidu | point | below | text:mp | `post:\mp` | channel volume level (absolute-volume-alist) | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.mf` | dynamic | common | mf | mf | mf lidu | point | below | text:mf | `post:\mf` | channel volume level (absolute-volume-alist) | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.f` | dynamic | common | f | f | f lidu | point | below | text:f | `post:\f` | channel volume level (absolute-volume-alist) | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.ff` | dynamic | common | ff | ff | ff lidu | point | below | text:ff | `post:\ff` | channel volume level (absolute-volume-alist) | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.fff` | dynamic | common | fff | fff | fff lidu | point | below | text:fff | `post:\fff` | channel volume level (absolute-volume-alist) | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.sf` | dynamic | common | sf（突強） | sf（突强） | sf tuqiang qiang | point | below | text:sf | `post:\jsSf` | +20 velocity on this note; level unchanged | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.fz` | dynamic | common | fz（突強） | fz（突强） | fz tuqiang qiang | point | below | text:fz | `post:\jsFz` | +20 velocity; level unchanged | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.sfz` | dynamic | common | sfz（突強） | sfz（突强） | sfz tuqiang qiang | point | below | text:sfz | `post:\jsSfz` | +20 velocity; level unchanged | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.sfzz` | dynamic | common | sfzz（特強突強） | sfzz（特强突强） | sfzz tuqiang qiang | point | below | text:sfzz | `post:\jsSfzz` | +40 velocity; level unchanged | ✓ seen [sheet_dynamic] | high | — |
| `dyn.fp` | dynamic | common | fp（強後即弱） | fp（强后即弱） | fp tuqiang qiang | point | below | text:fp | `post:\jsFp` | +20 velocity, then level p | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.sfp` | dynamic | common | sfp（突強後弱） | sfp（突强后弱） | sfp tuqiang qiang | point | below | text:sfp | `post:\jsSfp` | +20 velocity, then level p | ✓ seen [sheet_dynamic] | high | yes |
| `dyn.sfpp` | dynamic | common | sfpp（突強後極弱） | sfpp（突强后极弱） | sfpp tuqiang qiang | point | below | text:sfpp | `post:\jsSfpp` | +20 velocity, then level pp | ✓ seen [sheet_dynamic] | high | — |
| `dyn.rf` | dynamic | common | rf（加強） | rf（加强） | rf tuqiang qiang | point | below | text:rf | `post:\jsRf` | +20 velocity; level unchanged | ✓ seen [sheet_dynamic] | high | — |
| `dyn.rfz` | dynamic | common | rfz（加強） | rfz（加强） | rfz tuqiang qiang | point | below | text:rfz | `post:\jsRfz` | +20 velocity; level unchanged | ✓ seen [sheet_dynamic] | high | — |
| `hairpin.cresc` | dynamic | common | 漸強 | 渐强 | cresc jianqiang < | span | below | shape:hairpin_open | `span:post:\<¦post:\!` | volume ramps to the next dynamic (needs an end dynamic) | ✓ seen [sheet_dynamic] | high | yes |
| `hairpin.dim` | dynamic | common | 漸弱 | 渐弱 | dim decresc jianruo > | span | below | shape:hairpin_close | `span:post:\>¦post:\!` | volume ramps to the next dynamic | ✓ seen [sheet_dynamic] | high | yes |
| `hairpin.cresc_text` | dynamic | common | 漸強（cresc.） | 渐强（cresc.） | cresc jianqiang | span | below | text:cresc. | `span:post:\cresc¦post:\!` | as hairpin | ✓ seen [sheet_dynamic] | high | — |
| `hairpin.dim_text` | dynamic | common | 漸弱（dim.） | 渐弱（dim.） | dim jianruo | span | below | text:dim. | `span:post:\dim¦post:\!` | as hairpin | ✓ seen [sheet_dynamic] | high | — |
| `bar.dashed` | barline | common | 虛小節線 | 虚小节线 | xuxian dashed | bar | bar | shape:bar_dashed | `bar:end:\bar "!"` | none | ✓ seen [barlines_ok] | high | yes |
| `bar.double` | barline | common | 雙小節線 | 双小节线 | shuang double | bar | bar | shape:bar_double | `bar:end:\bar "¦¦"` | none | ✓ seen [barlines_ok] | high | yes |
| `bar.final` | barline | common | 終止線 | 终止线 | zhongzhi final end | bar | bar | shape:bar_final | `bar:end:\bar "¦."` | none | ✓ seen [f07_ds_al_coda] | high | yes |
| `bar.repeat_start` | barline | common | 反覆起 | 反复起 | fanfu repeat start | bar | bar | shape:repeat_start | `bar:start:R{` | unfolded (played twice) | ✓ seen [f04_volta] | high | yes |
| `bar.repeat_end` | barline | common | 反覆止 | 反复止 | fanfu repeat end | bar | bar | shape:repeat_end | `bar:end:}` | unfolded | ✓ seen [f04_volta] | high | yes |
| `bar.repeat_both` | barline | common | 雙向反覆 | 双向反复 | fanfu repeat both | bar | bar | shape:repeat_both | `bar:end:} + next bar start:R{` | unfolded | ✓ seen [repeat_endstart] | high | yes |
| `volta.1` | barline | common | 第一房 | 第一房 | yifang volta 1 house | bar | bar | shape:volta(1.) | `bar:A{ ... ¦ (first alternative)` | played on pass 1 | ✓ seen [f04_volta] | high | yes |
| `volta.2` | barline | common | 第二房 | 第二房 | erfang volta 2 house | bar | bar | shape:volta(2.) | `bar:... ¦ ... } (second alternative)` | played on pass 2 | ✓ seen [f04_volta] | high | yes |
| `volta.final` | barline | common | 結尾房 | 结尾房 | jiewei fang jie volta | bar | bar | shape:volta(結) | `bar:last alternative + label macro \jsVoltaJie` | as the last alternative | ✓ seen [f05_volta_jie] | medium | — |
| `nav.segno` | navigation | common | D.S. 記號（𝄋） | D.S. 记号（𝄋） | segno ds | bar | bar | shape:segno | `bar:start:Segno` | D.S. jumps here (unfolded) | ✓ seen [f07_ds_al_coda] | high | yes |
| `nav.tocoda` | navigation | common | To Coda（跳到尾聲） | To Coda（跳到尾声） | tocoda coda weisheng | bar | bar | shape:coda+text | `bar:end:ToCoda` | second pass jumps to Coda | ✓ seen [f07_ds_al_coda] | high | yes |
| `nav.ds` | navigation | common | D.S.（從記號處反覆） | D.S.（从记号处反复） | ds dal segno | bar | bar | text:D.S. al Coda | `bar:end:DS` | jump back to Segno (unfolded) | ✓ seen [f07_ds_al_coda] | high | yes |
| `nav.dc` | navigation | common | D.C.（從頭反覆） | D.C.（从头反复） | dc da capo congtou | bar | bar | text:D.C. (al Fine) | `bar:end:DC` | jump to start (unfolded) | ✓ seen [f06_dc_al_fine] | high | yes |
| `nav.fine` | navigation | common | Fine（結束） | Fine（结束） | fine jieshu | bar | bar | text:Fine | `bar:end:Fine` | ends here on the D.C./D.S. pass | ✓ seen [f06_dc_al_fine] | high | yes |
| `nav.coda_sign` | navigation | common | Coda 記號 ⊕（僅符號） | Coda 记号 ⊕（仅符号） | coda weisheng | bar | bar | shape:coda | `bar:start:\jsCodaSign` | none (sign only) | ✓ seen [f10_duet] | high | yes |
| `nav.segno_sign` | navigation | common | 記號（僅符號） | 记号（仅符号） | segno sign | bar | bar | shape:segno | `bar:start:\jsSegnoSign` | none | ✓ seen [nav_print_only] | high | yes |
| `nav.dc_text` | navigation | common | D.C.（僅文字） | D.C.（仅文字） | dc text | bar | bar | text:D.C. | `bar:end:\jsDCText` | none | ✓ seen [nav_print_only] | high | yes |
| `nav.dcfine_text` | navigation | common | D.C. al Fine（僅文字） | D.C. al Fine（仅文字） | dcfine text | bar | bar | text:D.C. al Fine | `bar:end:\jsDCFineText` | none | ✓ seen [nav_print_only] | high | — |
| `nav.ds_text` | navigation | common | D.S.（僅文字） | D.S.（仅文字） | ds text | bar | bar | text:D.S. | `bar:end:\jsDSText` | none | ✓ seen [nav_print_only] | high | yes |
| `nav.dscoda_text` | navigation | common | D.S. al Coda（僅文字） | D.S. al Coda（仅文字） | dscoda text | bar | bar | text:D.S. al Coda | `bar:end:\jsDSCodaText` | none | ✓ seen [nav_print_only] | high | — |
| `nav.fine_text` | navigation | common | Fine（僅文字） | Fine（仅文字） | fine text | bar | bar | text:Fine | `bar:end:\jsFineText` | none | ✓ seen [f10_duet] | high | yes |
| `nav.tocoda_text` | navigation | common | To Coda（僅文字） | To Coda（仅文字） | tocoda text | bar | bar | text:To Coda ⊕ | `bar:end:\jsToCodaText` | none | ✓ seen [nav_print_only] | high | — |
| `line.slur` | line | common | 連線（圓滑線） | 连线（圆滑线） | lianxian slur huaxian | span | above | shape:slur | `span:post:(¦post:)` | none (LilyPond MIDI ignores slurs) | ✓ seen [sheet_line] | high | yes |
| `line.tie` | line | common | 延音線 | 延音线 | yanyin tie | span | above | shape:tie | `after:~ (to next equal pitch)` | notes merged | ✓ seen [combo_tie_tuplet] | high | yes |
| `tuplet.3` | line | common | 三連音 | 三连音 | sanlianyin triplet | span | above | shape:tuplet(3) | `span:pre:3[¦after:]` | 2/3 duration | ✓ seen [combo_tie_tuplet] | high | yes |
| `line.8va` | line | common | 高八度（8va） | 高八度（8va） | 8va gaobadu ottava | span | above | shape:ottava(8va) | `span:pre:\jsOttavaUp¦after:\jsOttavaEnd` | +12 semitones (worker rewrites MIDI staff to \transpose c c') | ✓ seen [sheet_line] | high | — |
| `line.8vb` | line | common | 低八度（8vb） | 低八度（8vb） | 8vb dibadu ottava | span | below | shape:ottava(8vb) | `span:pre:\jsOttavaDown¦after:\jsOttavaEnd` | -12 semitones (MIDI rewrite) | ✓ seen [sheet_line] | high | — |
| `art.accent` | articulation | common | 重音 | 重音 | zhongyin accent > | point | above | shape:accent | `post:\accent` | +20 velocity | ✓ seen [sheet_articulation] | high | yes |
| `art.marcato` | articulation | common | 強重音 | 强重音 | qiangzhongyin marcato ^ | point | above | shape:marcato | `post:\marcato` | +40 velocity | ✓ seen [sheet_articulation] | high | — |
| `art.fermata` | articulation | common | 延長記號 | 延长记号 | yanchang fermata | point | above | shape:fermata | `post:\fermata` | none (MIDI does not lengthen) | ✓ seen [sheet_articulation] | high | yes |
| `art.tenuto` | articulation | common | 保持音 | 保持音 | baochi tenuto | point | above | shape:tenuto | `post:\tenuto` | none | ✓ seen [sheet_articulation] | high | yes |
| `art.staccato` | articulation | common | 頓音 ▼ | 顿音 ▼ | dunyin staccato tiaoyin | point | above | text:▼ | `post:\staccato` | shortened to 1/2 (max 0.5 s), +4 velocity | ✓ seen [sheet_articulation] | high | yes |
| `art.staccato_hollow` | articulation | common | 半頓音 ▽ | 半顿音 ▽ | bandunyin staccato | point | above | shape:triangle_down_hollow | `post:\jsStaccatoHollow` | portato: shortened to 3/4 | ✓ seen [sheet_articulation] | medium | yes |
| `art.staccatissimo` | articulation | common | 斷音（楔形） | 断音（楔形） | staccatissimo | point | above | shape:wedge | `post:\staccatissimo` | very short | ✓ seen [sheet_articulation] | high | — |
| `bow.up` | articulation | erhu | 推弓 V | 推弓 V | tuigong up bow V | point | above | shape:upbow | `post:\upbow` | none | ✓ seen [sheet_erhu] | high | yes |
| `bow.down` | articulation | erhu | 拉弓 ⊓ | 拉弓 ⊓ | lagong down bow | point | above | shape:downbow | `post:\downbow` | none | ✓ seen [sheet_erhu] | high | yes |
| `tempo.accel` | tempo | common | 漸快（accel.） | 渐快（accel.） | accel accel. | point | above | text:accel. | `post:\jsAccel` | none (MIDI tempo unchanged) | ✓ seen [sheet_tempo] | high | — |
| `tempo.rit` | tempo | common | 漸慢（rit.） | 渐慢（rit.） | rit rit. | point | above | text:rit. | `post:\jsRit` | none (MIDI tempo unchanged) | ✓ seen [sheet_tempo] | high | yes |
| `tempo.atempo` | tempo | common | 回原速（a tempo） | 回原速（a tempo） | atempo a tempo | point | above | text:a tempo | `post:\jsATempo` | none (MIDI tempo unchanged) | ✓ seen [sheet_tempo] | high | — |
| `tempo.accel_span` | tempo | common | 漸快（延伸線） | 渐快（延伸线） | accel_span accel. | span | above | text:accel. - - - | `span:post:\jsAccelSpan¦post:\stopTextSpan` | none | ✓ seen [sheet_tempo] | high | — |
| `tempo.rit_span` | tempo | common | 漸慢（延伸線） | 渐慢（延伸线） | rit_span rit. | span | above | text:rit. - - - | `span:post:\jsRitSpan¦post:\stopTextSpan` | none | ✓ seen [sheet_tempo] | high | — |
| `pedal.on` | pedal | common | 踏板踩下 | 踏板踩下 | taban pedal ped | point | below | text:Ped. | `post:\sustainOn` | CC64 127 (sustain on) | ✓ seen [sheet_pedal] | high | — |
| `pedal.off` | pedal | common | 踏板放開 | 踏板放开 | taban pedal * | point | below | text:* | `post:\sustainOff` | CC64 0 | ✓ seen [sheet_pedal] | high | — |
| `breath.comma` | articulation | common | 換氣（逗號） | 换气（逗号） | huanqi breath | point | right | text:, | `after:\breathe` | none | ✓ seen [sheet_articulation] | high | yes |
| `breath.v` | articulation | dizi | 換氣 V | 换气 V | huanqi breath V | point | right | text:V | `after:\jsBreathV` | none | ✓ seen [sheet_dizi] | high | yes |
| `harmonic` | articulation | common | 泛音 ○ | 泛音 ○ | fanyin harmonic o | point | above | shape:circle | `post:\flageolet` | none | ✓ seen [sheet_articulation] | high | yes |
| `orn.prall` | ornament | common | 上波音 | 上波音 | prall boyin chanyin | point | above | shape:prall | `post:\prall` | none (not realised) | ✓ seen [sheet_ornament] | high | yes |
| `orn.mordent` | ornament | common | 下波音 | 下波音 | mordent boyin chanyin | point | above | shape:mordent | `post:\mordent` | none (not realised) | ✓ seen [sheet_ornament] | high | yes |
| `orn.prallprall` | ornament | common | 複波音（長波音） | 复波音（长波音） | prallprall boyin chanyin | point | above | shape:prallprall | `post:\prallprall` | none (not realised) | ✓ seen [sheet_ornament] | high | partial |
| `orn.turn` | ornament | common | 回音 | 回音 | turn boyin chanyin | point | above | shape:turn | `post:\turn` | none (not realised) | ✓ seen [sheet_ornament] | high | yes |
| `orn.trill` | ornament | common | 顫音 tr | 颤音 tr | trill boyin chanyin | point | above | shape:trill | `post:\trill` | none (not realised) | ✓ seen [sheet_ornament] | high | yes |
| `orn.prall_sharp` | ornament | common | 上波音帶♯ | 上波音带♯ | prall sharp boyin | point | above | shape:prall+sharp | `post:\jsPrallSharp` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.prall_flat` | ornament | common | 上波音帶♭ | 上波音带♭ | prall flat boyin | point | above | shape:prall+flat | `post:\jsPrallFlat` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.prall_natural` | ornament | common | 上波音帶♮ | 上波音带♮ | prall natural boyin | point | above | shape:prall+natural | `post:\jsPrallNatural` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.mordent_sharp` | ornament | common | 下波音帶♯ | 下波音带♯ | mordent sharp boyin | point | above | shape:mordent+sharp | `post:\jsMordentSharp` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.mordent_flat` | ornament | common | 下波音帶♭ | 下波音带♭ | mordent flat boyin | point | above | shape:mordent+flat | `post:\jsMordentFlat` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.mordent_natural` | ornament | common | 下波音帶♮ | 下波音带♮ | mordent natural boyin | point | above | shape:mordent+natural | `post:\jsMordentNatural` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.trill_sharp` | ornament | common | 顫音帶♯ | 颤音带♯ | trill sharp boyin | point | above | shape:trill+sharp | `post:\jsTrillSharp` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.trill_flat` | ornament | common | 顫音帶♭ | 颤音带♭ | trill flat boyin | point | above | shape:trill+flat | `post:\jsTrillFlat` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.trill_natural` | ornament | common | 顫音帶♮ | 颤音带♮ | trill natural boyin | point | above | shape:trill+natural | `post:\jsTrillNatural` | none | ✓ seen [sheet_ornament] | high | — |
| `orn.trill_span` | ornament | common | 顫音（tr～～） | 颤音（tr～～） | trill tr wavy chanyin | span | above | shape:trill_span | `span:post:\startTrillSpan¦post:\stopTrillSpan` | none | ✓ seen [sheet_ornament] | high | yes |
| `trem.1` | ornament | common | 震音（一條） | 震音（一条） | zhenyin tremolo 1 | point | below | shape:tremolo(1) | `post:\jsTremOne` | none | ✓ seen [sheet_ornament] | high | partial |
| `trem.2` | ornament | common | 震音（二條） | 震音（二条） | zhenyin tremolo 2 | point | below | shape:tremolo(2) | `post:\jsTremTwo` | none | ✓ seen [sheet_ornament] | high | partial |
| `trem.3` | ornament | common | 震音（三條） | 震音（三条） | zhenyin tremolo 3 | point | below | shape:tremolo(3) | `tok:///` | 32nd-note stem tremolo in MIDI staff: see probe | ✓ seen [sheet_ornament] | high | yes |
| `trem.4` | ornament | common | 震音（四條） | 震音（四条） | zhenyin tremolo 4 | point | below | shape:tremolo(4) | `post:\jsTremFour` | none | ✓ seen [sheet_ornament] | high | partial |
| `arp.plain` | ornament | common | 琶音 | 琶音 | payin arpeggio plain | pre | left | shape:arpeggio | `pre:arp` | none (single note); chord only in source mode | ✓ seen [sheet_ornament] | high | yes |
| `arp.up` | ornament | common | 琶音（向上） | 琶音（向上） | payin arpeggio up | pre | left | shape:arpeggio | `pre:arpUp` | none (single note); chord only in source mode | ✓ seen [sheet_ornament] | high | yes |
| `arp.down` | ornament | common | 琶音（向下） | 琶音（向下） | payin arpeggio down | pre | left | shape:arpeggio | `pre:arpDown` | none (single note); chord only in source mode | ✓ seen [sheet_ornament] | high | yes |
| `erhu.inner` | technique | erhu | 內弦 | 内弦 | neixian inner string 内 | point | above | text:內/内 | `post:\jsErhuInner` | none | ✓ seen [sheet_erhu] | medium | yes |
| `erhu.outer` | technique | erhu | 外弦 | 外弦 | waixian outer string 外 | point | above | text:外 | `post:\jsErhuOuter` | none | ✓ seen [sheet_erhu] | medium | yes |
| `finger.0` | fingering | erhu | 空弦 0 | 空弦 0 | zhifa finger 0 kongxian open | point | above | text:0 | `post:\jsFingerZero` | none | ✓ seen [sheet_erhu] | medium | yes |
| `finger.1` | fingering | erhu | 一指 | 一指 | zhifa finger 1  | point | above | text:一 | `post:Fr=1` | none | ✓ seen [sheet_erhu] | high | yes |
| `finger.2` | fingering | erhu | 二指 | 二指 | zhifa finger 2  | point | above | text:二 | `post:Fr=2` | none | ✓ seen [sheet_erhu] | high | yes |
| `finger.3` | fingering | erhu | 三指 | 三指 | zhifa finger 3  | point | above | text:三 | `post:Fr=3` | none | ✓ seen [sheet_erhu] | high | yes |
| `finger.4` | fingering | erhu | 四指 | 四指 | zhifa finger 4  | point | above | text:四 | `post:Fr=4` | none | ✓ seen [sheet_erhu] | high | yes |
| `finger.5` | fingering | erhu | 五指 | 五指 | zhifa finger 5  | point | above | text:五 | `post:Fr=五` | none | ✓ seen [sheet_erhu] | high | yes |
| `slide.up` | technique | erhu | 上滑音 | 上滑音 | shanghua slide up huayin | pre | left | text:↗ | `pre:slideUp` | none | ✓ seen [sheet_erhu] | medium | yes |
| `slide.down` | technique | erhu | 下滑音 | 下滑音 | xiahua slide down huayin | pre | left | text:↘ | `pre:slideDown` | none | ✓ seen [sheet_erhu] | medium | yes |
| `slide.back` | technique | erhu | 回滑音 | 回滑音 | huihua slide back | pre | left | text:⌒ | `pre:slide=⌒` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_erhu] | low | partial |
| `slide.pad` | technique | erhu | 墊指滑音 | 垫指滑音 | dianzhi huayin | composite | left | grace + text:↗ | `grace_before + pre:slideUp` | none | ✓ seen · DEFERRED: teacher confirmation [combo_grace] | low | — |
| `erhu.vibrato` | technique | erhu | 揉弦 | 揉弦 | rouxian vibrato | point | above | text:～ | `post:\jsErhuVibrato` | none | ✓ seen [sheet_erhu] | medium | — |
| `erhu.press_vib` | technique | erhu | 壓揉 | 压揉 | yarou press vibrato | point | above | text:壓揉/压揉 | `post:\jsErhuPressVib` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_erhu] | low | — |
| `erhu.slide_vib` | technique | erhu | 滑揉 | 滑揉 | huarou slide vibrato | point | above | text:滑揉 | `post:\jsErhuSlideVib` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_erhu] | low | — |
| `erhu.harm_art` | technique | erhu | 人工泛音 ◇ | 人工泛音 ◇ | rengong fanyin artificial harmonic | point | above | shape:diamond | `post:\jsHarmArtificial` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_erhu] | low | — |
| `erhu.trem_bow` | technique | erhu | 顫弓 | 颤弓 | chengong tremolo bow | point | below | shape:tremolo(3) | `tok:///` | see trem.3 | ✓ seen [sheet_erhu] | medium | yes |
| `erhu.dun` | technique | erhu | 頓弓 | 顿弓 | dungong | point | above | text:頓弓/顿弓 | `post:\jsErhuDun` | none | ✓ seen [sheet_erhu] | medium | — |
| `erhu.pao` | technique | erhu | 拋弓 | 抛弓 | paogong | point | above | text:拋弓/抛弓 | `post:\jsErhuPao` | none | ✓ seen [sheet_erhu] | medium | — |
| `erhu.tiao` | technique | erhu | 跳弓 | 跳弓 | tiaogong | point | above | text:跳弓/跳弓 | `post:\jsErhuTiao` | none | ✓ seen [sheet_erhu] | medium | — |
| `erhu.liandun` | technique | erhu | 連頓弓 | 连顿弓 | liandungong | point | above | text:連頓弓/连顿弓 | `post:\jsErhuLiandun` | none | ✓ seen [sheet_erhu] | medium | — |
| `erhu.shift` | technique | erhu | 換把 | 换把 | huanba shift position | point | above | text:換把/换把 | `post:\jsErhuShift` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_erhu] | low | — |
| `erhu.da` | technique | erhu | 打音 | 打音 | dayin | point | above | text:打音/打音 | `post:\jsErhuDa` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_erhu] | low | — |
| `erhu.pizz_left` | technique | erhu | 左手撥弦 ＋ | 左手拨弦 ＋ | zuoshou boxian pizz + | point | above | text:+ | `post:\jsPizzLeft` | none | ✓ seen [sheet_erhu] | medium | yes |
| `erhu.pizz_right` | technique | erhu | 右手撥弦 | 右手拨弦 | youshou boxian pizz | point | above | text:撥/拨 | `post:\jsPizzRight` | none | ✓ seen [sheet_erhu] | medium | — |
| `gz.tuo` | technique | guzheng | 托 | 托 | guzheng tuo | point | above | text:∟ | `post:\jsGzTuo` | none | ✓ seen [sheet_guzheng] | medium | yes |
| `gz.pi` | technique | guzheng | 劈 | 劈 | guzheng pi | point | above | text:┐ | `post:\jsGzPi` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_guzheng] | low | — |
| `gz.mo` | technique | guzheng | 抹 | 抹 | guzheng mo | point | above | text:丶 | `post:\jsGzMo` | none | ✓ seen [sheet_guzheng] | medium | partial |
| `gz.tiao` | technique | guzheng | 挑 | 挑 | guzheng tiao | point | above | text:／ | `post:\jsGzTiao` | none | ✓ seen [sheet_guzheng] | medium | partial |
| `gz.gou` | technique | guzheng | 勾 | 勾 | guzheng gou | point | above | text:∧ | `post:\jsGzGou` | none | ✓ seen [sheet_guzheng] | medium | — |
| `gz.ti` | technique | guzheng | 剔 | 剔 | guzheng ti | point | above | text:∨ | `post:\jsGzTi` | none | ✓ seen [sheet_guzheng] | medium | — |
| `gz.tl` | technique | guzheng | 提 | 提 | guzheng tl | point | above | text:提 | `post:\jsGzTl` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_guzheng] | low | — |
| `gz.dacuo` | technique | guzheng | 大撮 | 大撮 | guzheng dacuo | point | above | text:大撮 | `post:\jsGzDacuo` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_guzheng] | low | — |
| `gz.xiaocuo` | technique | guzheng | 小撮 | 小撮 | guzheng xiaocuo | point | above | text:小撮 | `post:\jsGzXiaocuo` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_guzheng] | low | — |
| `gz.yao` | technique | guzheng | 搖指 | 摇指 | guzheng yao | point | above | text:‖ | `post:\jsGzYao` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_guzheng] | low | — |
| `gz.huazhi` | technique | guzheng | 花指 | 花指 | guzheng huazhi | point | above | text:※ | `post:\jsGzHuazhi` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_guzheng] | low | — |
| `gz.an` | technique | guzheng | 按音 | 按音 | guzheng an | point | above | text:按 | `post:\jsGzAn` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_guzheng] | low | partial |
| `gz.chan` | technique | guzheng | 顫音 | 颤音 | guzheng chan | point | above | text:～ | `post:\jsGzChan` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_guzheng] | low | — |
| `gz.gua_up` | technique | guzheng | 刮奏（上行） | 刮奏（上行） | guazou glissando up | point | above | text:↗ | `post:\jsGzGuaUp` | none | ✓ seen [sheet_guzheng] | medium | partial |
| `gz.gua_down` | technique | guzheng | 刮奏（下行） | 刮奏（下行） | guazou glissando down | point | above | text:↘ | `post:\jsGzGuaDown` | none | ✓ seen [sheet_guzheng] | medium | partial |
| `pipa.tan` | technique | pipa | 彈 | 弹 | pipa tan | point | above | text:丶 | `post:\jsPipaTan` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_pipa] | low | partial |
| `pipa.tiao` | technique | pipa | 挑 | 挑 | pipa tiao | point | above | text:／ | `post:\jsPipaTiao` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_pipa] | low | partial |
| `pipa.lun` | technique | pipa | 輪指 | 轮指 | pipa lun | point | above | text:※ | `post:\jsPipaLun` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_pipa] | low | — |
| `pipa.sao` | technique | pipa | 掃 | 扫 | pipa sao | point | above | text:掃/扫 | `post:\jsPipaSao` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_pipa] | low | — |
| `pipa.fu` | technique | pipa | 拂 | 拂 | pipa fu | point | above | text:拂 | `post:\jsPipaFu` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_pipa] | low | — |
| `pipa.yao` | technique | pipa | 搖 | 摇 | pipa yao | point | above | text:搖/摇 | `post:\jsPipaYao` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_pipa] | low | — |
| `pipa.gun` | technique | pipa | 滾 | 滚 | pipa gun | point | above | text:滾/滚 | `post:\jsPipaGun` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_pipa] | low | — |
| `pipa.tui` | technique | pipa | 推 | 推 | pipa tui | point | above | text:推 | `post:\jsPipaTui` | none | ✓ seen [sheet_pipa] | medium | — |
| `pipa.la` | technique | pipa | 拉 | 拉 | pipa la | point | above | text:拉 | `post:\jsPipaLa` | none | ✓ seen [sheet_pipa] | medium | — |
| `pipa.yin` | technique | pipa | 吟 | 吟 | pipa yin | point | above | text:吟 | `post:\jsPipaYin` | none | ✓ seen [sheet_pipa] | medium | — |
| `pipa.rou` | technique | pipa | 揉 | 揉 | pipa rou | point | above | text:揉 | `post:\jsPipaRou` | none | ✓ seen [sheet_pipa] | medium | — |
| `pipa.daiqi` | technique | pipa | 帶起 | 带起 | pipa daiqi | point | above | text:帶/带 | `post:\jsPipaDaiqi` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_pipa] | low | — |
| `dizi.tu1` | technique | dizi | 單吐 | 单吐 | dizi xiao tu1 | point | above | text:T | `post:\jsDiziTuOne` | none | ✓ seen [sheet_dizi] | high | yes |
| `dizi.tu2` | technique | dizi | 雙吐 | 双吐 | dizi xiao tu2 | point | above | text:TK | `post:\jsDiziTuTwo` | none | ✓ seen [sheet_dizi] | high | yes |
| `dizi.tu3` | technique | dizi | 三吐 | 三吐 | dizi xiao tu3 | point | above | text:TTK | `post:\jsDiziTuThree` | none | ✓ seen [sheet_dizi] | high | yes |
| `dizi.huashe` | technique | dizi | 花舌 | 花舌 | dizi xiao huashe | point | above | text:＊ | `post:\jsDiziHuashe` | none | ✓ seen [sheet_dizi] | medium | partial |
| `dizi.die` | technique | dizi | 疊音 | 叠音 | dizi xiao die | point | above | text:又 | `post:\jsDiziDie` | none | ✓ seen [sheet_dizi] | medium | yes |
| `dizi.da` | technique | dizi | 打音 | 打音 | dizi xiao da | point | above | text:丁 | `post:\jsDiziDa` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_dizi] | low | yes |
| `dizi.li_up` | technique | dizi | 歷音（上） | 历音（上） | dizi xiao li_up | point | above | text:⤴ | `post:\jsDiziLiUp` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_dizi] | low | — |
| `dizi.li_down` | technique | dizi | 歷音（下） | 历音（下） | dizi xiao li_down | point | above | text:⤵ | `post:\jsDiziLiDown` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_dizi] | low | — |
| `dizi.qizhen` | technique | dizi | 氣震音 | 气震音 | dizi xiao qizhen | point | above | text:氣震/气震 | `post:\jsDiziQizhen` | none | ✓ seen · DEFERRED: teacher confirmation [sheet_dizi] | low | — |
| `dizi.zeng` | technique | dizi | 贈音 | 赠音 | zengyin | composite | right | grace after | `grace_after (existing field)` | none | ✓ seen [combo_grace] | medium | — |
| `bow.down_hook` | articulation | erhu | 拉弓（京胡式） | 拉弓（京胡式） | lagong jinghu hook | point | above | shape:hook_right_down | `post:\jsBowDownHook` | none | ✓ seen [sheet_erhu] | medium | yes |
| `bow.up_hook` | articulation | erhu | 推弓（京胡式） | 推弓（京胡式） | tuigong jinghu hook | point | above | shape:hook_left_down | `post:\jsBowUpHook` | none | ✓ seen [sheet_erhu] | medium | yes |
| `slide.gliss` | technique | common | 大滑音（滑奏線） | 大滑音（滑奏线） | dahuayin glissando glis | span | between | shape:gliss_line | `span:pre:glis¦(next note)` | none (LilyPond MIDI ignores glissando) | ✓ seen [sheet_technique] | medium | yes |
| `breath.v_bold` | articulation | dizi | 換氣（粗 V） | 换气（粗 V） | huanqi breath bold | point | right | text:V(bold) | `after:\jsBreathVBold` | none | ✓ seen [sheet_dizi] | high | yes |
| `caesura` | articulation | common | 休止記號 //（停頓） | 休止记号 //（停顿） | xiuzhi caesura tingdun // | point | right | text:// | `after:\jsCaesura` | none | ✓ seen [sheet_articulation] | high | yes |
| `orn.reverseturn` | ornament | common | 逆回音 | 逆回音 | nihuiyin reverse turn | point | above | shape:reverseturn | `post:\reverseturn` | none | ✓ seen [sheet_ornament] | high | yes |
| `tempo.sanban` | tempo | common | 散板 | 散板 | sanban free meter | point | above | shape:sanban(卄-like) | `post:\jsSanban` | none | ✓ seen [sheet_tempo] | high | yes |
| `dizi.k` | technique | dizi | 吐音 K | 吐音 K | tuyin K | point | above | text:K | `post:\jsDiziK` | none | ✓ seen [sheet_dizi] | high | yes |
| `dizi.duo` | technique | dizi | 剁音 | 剁音 | duoyin | point | above | shape:duo(2 lines + bent arrow) | `post:\jsDiziDuo` | none | ✓ seen [sheet_dizi] | medium | yes |
| `dizi.circular` | technique | dizi | 循環換氣 | 循环换气 | xunhuan huanqi circular breathing | point | above | text:(∞) | `post:\jsDiziCircular` | none | ✓ seen [sheet_dizi] | medium | yes |
| `gz.shuangtuo` | technique | guzheng | 雙托 | 双托 | guzheng shuangtuo | point | above | text:∟∟(stacked) | `post:\jsGzShuangtuo` | none | ✓ seen [sheet_guzheng] | medium | yes |
| `stroke.lun` | technique | pipa | 輪指（斜桿三切線） | 轮指（斜杆三切线） | lunzhi pipa ruan liuqin | point | above | shape:stroke_bars(3) | `post:\jsStrokeLun` | none | ✓ seen [sheet_pipa] | medium | partial |
| `stroke.yao` | technique | guzheng | 搖指（斜桿雙切線） | 摇指（斜杆双切线） | yaozhi guzheng pipa | point | above | shape:stroke_bars(2) | `post:\jsStrokeYao` | none | ✓ seen [sheet_guzheng] | medium | partial |
| `ruan.lun3` | technique | ruan | 中阮輪指（三輪） | 中阮轮指（三轮） | zhongruan lunzhi 3 | point | above | shape:leaves(3) | `post:\jsRuanLunThree` | none | ✓ seen [sheet_ruan] | high | yes |
| `ruan.lun4` | technique | ruan | 中阮輪指（四輪） | 中阮轮指（四轮） | zhongruan lunzhi 4 | point | above | shape:leaves(4) | `post:\jsRuanLunFour` | none | ✓ seen [sheet_ruan] | high | yes |
| `hulusi.close` | technique | hulusi | 關閉副管 | 关闭副管 | hulusi fuguan close | point | above | shape:circle_arrow_right | `post:\jsHulusiClose` | none | ✓ seen [sheet_hulusi] | high | yes |
| `hulusi.xuzhi` | technique | hulusi | 虛指顫音（圈波浪） | 虚指颤音（圈波浪） | xuzhi chanyin hulusi | span | above | shape:circle+wavy | `span:post:\jsXuzhiSpan¦post:\stopTrillSpan` | none | ✓ seen [sheet_hulusi] | medium | yes |
| `line.wavy` | line | common | 橫波浪線 | 横波浪线 | bolangxian wavy line | span | above | shape:wavy | `span:post:\jsWavySpan¦post:\stopTrillSpan` | none | ✓ seen [sheet_line] | medium | partial |
| `string.1` | fingering | common | 圈一（弦序） | 圈一（弦序） | quan 1 xianxu circled | point | above | shape:circled(一) | `post:\jsStringOne` | none | ✓ seen [sheet_fingering] | medium | yes |
| `string.2` | fingering | common | 圈二（弦序） | 圈二（弦序） | quan 2 xianxu circled | point | above | shape:circled(二) | `post:\jsStringTwo` | none | ✓ seen [sheet_fingering] | medium | yes |
| `string.3` | fingering | common | 圈三（弦序） | 圈三（弦序） | quan 3 xianxu circled | point | above | shape:circled(三) | `post:\jsStringThree` | none | ✓ seen [sheet_fingering] | medium | yes |
| `string.4` | fingering | common | 圈四（弦序） | 圈四（弦序） | quan 4 xianxu circled | point | above | shape:circled(四) | `post:\jsStringFour` | none | ✓ seen [sheet_fingering] | medium | yes |
| `string.5` | fingering | common | 圈五（弦序） | 圈五（弦序） | quan 5 xianxu circled | point | above | shape:circled(五) | `post:\jsStringFive` | none | ✓ seen [sheet_fingering] | medium | yes |
| `yangqin.shuangtanlun` | technique | yangqin | 雙彈輪 | 双弹轮 | yangqin shuangtanlun | point | above | shape:vbar_2cross | `post:\jsYangqinShuangtanlun` | none | ✓ seen [sheet_yangqin] | high | yes |

### 2.2 數量
- entries: 175; by group: {'common': 96, 'erhu': 29, 'dizi': 15, 'guzheng': 17, 'pipa': 13, 'ruan': 2, 'hulusi': 2, 'yangqin': 1}
- confidence: {'high': 104, 'medium': 44, 'low': 27} (low = deferred, needs teacher confirmation)
- JP-Word parity over our ids: {'yes': 95, '—': 64, 'partial': 16}; JP-Word med/high items deferred: 16 groups

### 2.3 JP-Word 中／高信心但延後的項目
- F50E ┐ 勾或劈 — JP-Word itself ambiguous (勾 vs 劈); our gz.pi kept deferred
- F310–F315, F34A, F4C3/F4C4, F5D5–F5DA, F5E3–F5E6 arc/hook slide variants — mapped to slide.up/down/back (partial); exact arc shapes deferred
- F318/F319 大滑音 straight long arrows — mapped to slide.gliss (LilyPond glissando line)
- F51F–F523, F5CE–F5D0 star / 锣鼓 marks — percussion (锣鼓經) out of scope
- F526–F529 small arc / parentheses (伴奏記號) — deferred
- F52A–F52F single-stroke angle variants — pipa.tan/pipa.tiao/gz.mo/gz.tiao cover / and 丶; angle variants deferred
- F4A2/F4A3 mini hairpins — covered by hairpin spans
- F5C0–F5C9, F488–F48D half-hole states — wind fingering charts out of scope
- F5E8/F5E9, F5EC–F5F8 sweep/刮奏 arrow sets — gz.gua_up/down (straight arrows) only
- F4D8, F250–F25C accordion — out of scope
- F5BD–F5BF generic geometric shapes — deferred (no musical meaning)
- core: 横波浪线 sawtooth / semicircle — line.wavy sine only
- core: 上/下滑音 wavy-line form — deferred
- core: 微升/微降 — deferred (microtones)
- 核心: 左右圆括号 — deferred
- 古筝花指（正板花/板前花）, 汇组方框 — deferred (note-replacing / boxed group)

### 2.4 validate_source 白名單新增（由登記表產生）
cresc, decresc, dim, fp, fz, jsATempo, jsAccel, jsAccelSpan, jsBowDownHook, jsBowUpHook, jsBreathV, jsBreathVBold, jsCaesura, jsCodaSign, jsDCFineText, jsDCText, jsDSCodaText, jsDSText, jsDiziCircular, jsDiziDa, jsDiziDie, jsDiziDuo, jsDiziHuashe, jsDiziK, jsDiziLiDown, jsDiziLiUp, jsDiziQizhen, jsDiziTuOne, jsDiziTuThree, jsDiziTuTwo, jsErhuDa, jsErhuDun, jsErhuInner, jsErhuLiandun, jsErhuOuter, jsErhuPao, jsErhuPressVib, jsErhuShift, jsErhuSlideVib, jsErhuTiao, jsErhuVibrato, jsFineText, jsFingerZero, jsFp, jsFz, jsGzAn, jsGzChan, jsGzDacuo, jsGzGou, jsGzGuaDown, jsGzGuaUp, jsGzHuazhi, jsGzMo, jsGzPi, jsGzShuangtuo, jsGzTi, jsGzTiao, jsGzTl, jsGzTuo, jsGzXiaocuo, jsGzYao, jsHarmArtificial, jsHulusiClose, jsMordentFlat, jsMordentNatural, jsMordentSharp, jsOttavaDown, jsOttavaEnd, jsOttavaUp, jsPipaDaiqi, jsPipaFu, jsPipaGun, jsPipaLa, jsPipaLun, jsPipaRou, jsPipaSao, jsPipaTan, jsPipaTiao, jsPipaTui, jsPipaYao, jsPipaYin, jsPizzLeft, jsPizzRight, jsPrallFlat, jsPrallNatural, jsPrallSharp, jsRf, jsRfz, jsRit, jsRitSpan, jsRuanLunFour, jsRuanLunThree, jsSanban, jsSegnoSign, jsSf, jsSfp, jsSfpp, jsSfz, jsSfzz, jsStaccatoHollow, jsStringFive, jsStringFour, jsStringOne, jsStringThree, jsStringTwo, jsStrokeLun, jsStrokeYao, jsToCodaText, jsTremFour, jsTremOne, jsTremTwo, jsTrillFlat, jsTrillNatural, jsTrillSharp, jsVoltaJie, jsWavySpan, jsXuzhiSpan, jsYangqinShuangtanlun, prallprall, reverseturn, rfz, sf, sfp, startTrillSpan, stopTextSpan, stopTrillSpan, sustainOff, sustainOn
(128 names)

### 2.5 prelude 全文（worker 插入，繁體版；簡體版只換技法字）
```lilypond
%% ---- Jianpu Studio prelude (app-generated; not user input) ----
\layout { \context { \RhythmicStaff \consists Ottava_spanner_engraver \consists Piano_pedal_engraver \consists Piano_pedal_align_engraver ottavationMarkups = #ottavation-ordinals \override SustainPedalLineSpanner.staff-padding = #3.5 } }
\layout { \context { \Voice \override Fingering.font-encoding = #'latin1 \override Fingering.font-size = #2 } }
#(set! absolute-volume-alist (append '(("sf" . #f) ("fp" . 0.55) ("sfp" . 0.55) ("sfpp" . 0.49)) absolute-volume-alist))
jsSf = #(make-dynamic-script "sf")
jsFz = #(make-dynamic-script "fz")
jsSfz = #(make-dynamic-script "sfz")
jsSfzz = #(make-dynamic-script "sfzz")
jsFp = #(make-dynamic-script "fp")
jsSfp = #(make-dynamic-script "sfp")
jsSfpp = #(make-dynamic-script "sfpp")
jsRf = #(make-dynamic-script "rf")
jsRfz = #(make-dynamic-script "rfz")
jsVoltaJie = \once \override Score.VoltaBracket.text = \markup "結"
jsCodaSign = \textMark \markup \musicglyph "scripts.coda"
jsSegnoSign = \textMark \markup \musicglyph "scripts.segno"
jsDCText = \textEndMark \markup \italic "D.C."
jsDCFineText = \textEndMark \markup \italic "D.C. al Fine"
jsDSText = \textEndMark \markup \italic "D.S."
jsDSCodaText = \textEndMark \markup \italic "D.S. al Coda"
jsFineText = \textEndMark \markup \italic "Fine"
jsToCodaText = \textEndMark \markup { \italic "To Coda" \musicglyph "scripts.coda" }
jsOttavaUp = \ottava #1
jsOttavaEnd = \ottava #0
jsOttavaDown = { \ottava #-1 \set Staff.ottavation = \markup \concat { "8" \tiny "vb" } }
jsStaccatoHollow = -\tweak stencil #ly:text-interface::print -\tweak text \markup \scale #'(0.85 . -0.85) \triangle ##f \portato
jsAccel = ^\markup \italic "accel."
jsRit = ^\markup \italic "rit."
jsATempo = ^\markup \italic "a tempo"
jsAccelSpan = -\tweak bound-details.left.text \markup \italic "accel. " ^\startTextSpan
jsRitSpan = -\tweak bound-details.left.text \markup \italic "rit. " ^\startTextSpan
jsBreathV = { \once \override BreathingSign.text = \markup \fontsize #-1 \sans "V" \breathe }
jsPrallSharp = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.sharp" \musicglyph "scripts.prall" }
jsPrallFlat = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.flat" \musicglyph "scripts.prall" }
jsPrallNatural = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.natural" \musicglyph "scripts.prall" }
jsMordentSharp = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.sharp" \musicglyph "scripts.mordent" }
jsMordentFlat = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.flat" \musicglyph "scripts.mordent" }
jsMordentNatural = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.natural" \musicglyph "scripts.mordent" }
jsTrillSharp = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.sharp" \musicglyph "scripts.trill" }
jsTrillFlat = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.flat" \musicglyph "scripts.trill" }
jsTrillNatural = ^\markup \override #'(baseline-skip . 1.4) \center-column { \fontsize #-4 \musicglyph "accidentals.natural" \musicglyph "scripts.trill" }
jsTremOne = -\tweak outside-staff-priority ##f -\tweak avoid-slur #'inside _\markup { \with-dimensions #'(0 . 0) #'(2.5 . 2.1) \postscript "1.1 0.4 moveto 2.1 1.4 lineto stroke" }
jsTremTwo = -\tweak outside-staff-priority ##f -\tweak avoid-slur #'inside _\markup { \with-dimensions #'(0 . 0) #'(2.5 . 2.1) \postscript "1.1 0.4 moveto 2.1 1.4 lineto 1.3 0.2 moveto 2.3 1.2 lineto stroke" }
jsTremFour = -\tweak outside-staff-priority ##f -\tweak avoid-slur #'inside _\markup { \with-dimensions #'(0 . 0) #'(2.5 . 2.1) \postscript "1.1 0.4 moveto 2.1 1.4 lineto 1.3 0.2 moveto 2.3 1.2 lineto 1.5 0.0 moveto 2.5 1.0 lineto 1.7 -0.2 moveto 2.7 0.8 lineto stroke" }
jsErhuInner = ^\markup \fontsize #-2 "內"
jsErhuOuter = ^\markup \fontsize #-2 "外"
jsFingerZero = -\finger \markup \bold "0"
jsErhuVibrato = ^\markup \fontsize #0 "～"
jsErhuPressVib = ^\markup \fontsize #-2 "壓揉"
jsErhuSlideVib = ^\markup \fontsize #-2 "滑揉"
jsHarmArtificial = ^\markup \fontsize #-2 \musicglyph "noteheads.s0harmonic"
jsErhuDun = ^\markup \fontsize #-2 "頓弓"
jsErhuPao = ^\markup \fontsize #-2 "拋弓"
jsErhuTiao = ^\markup \fontsize #-2 "跳弓"
jsErhuLiandun = ^\markup \fontsize #-2 "連頓弓"
jsErhuShift = ^\markup \fontsize #-2 "換把"
jsErhuDa = ^\markup \fontsize #-2 "打"
jsPizzLeft = ^\markup \fontsize #-1 \bold "+"
jsPizzRight = ^\markup \fontsize #-2 "撥"
jsGzTuo = ^\markup \fontsize #-2 "∟"
jsGzPi = ^\markup \fontsize #-2 "┐"
jsGzMo = ^\markup \fontsize #-2 "丶"
jsGzTiao = ^\markup \fontsize #-2 "／"
jsGzGou = ^\markup \fontsize #-2 "∧"
jsGzTi = ^\markup \fontsize #-2 "∨"
jsGzTl = ^\markup \fontsize #-2 "提"
jsGzDacuo = ^\markup \fontsize #-2 "大撮"
jsGzXiaocuo = ^\markup \fontsize #-2 "小撮"
jsGzYao = ^\markup \fontsize #-2 "‖"
jsGzHuazhi = ^\markup \fontsize #-2 "※"
jsGzAn = ^\markup \fontsize #-2 "按"
jsGzChan = ^\markup \fontsize #-2 "～"
jsGzGuaUp = ^\markup \fontsize #-2 "↗"
jsGzGuaDown = ^\markup \fontsize #-2 "↘"
jsPipaTan = ^\markup \fontsize #-2 "丶"
jsPipaTiao = ^\markup \fontsize #-2 "／"
jsPipaLun = ^\markup \fontsize #-2 "※"
jsPipaSao = ^\markup \fontsize #-2 "掃"
jsPipaFu = ^\markup \fontsize #-2 "拂"
jsPipaYao = ^\markup \fontsize #-2 "搖"
jsPipaGun = ^\markup \fontsize #-2 "滾"
jsPipaTui = ^\markup \fontsize #-2 "推"
jsPipaLa = ^\markup \fontsize #-2 "拉"
jsPipaYin = ^\markup \fontsize #-2 "吟"
jsPipaRou = ^\markup \fontsize #-2 "揉"
jsPipaDaiqi = ^\markup \fontsize #-2 "帶"
jsDiziTuOne = ^\markup \fontsize #-2 "T"
jsDiziTuTwo = ^\markup \fontsize #-2 "TK"
jsDiziTuThree = ^\markup \fontsize #-2 "TTK"
jsDiziHuashe = ^\markup \fontsize #-2 "＊"
jsDiziDie = ^\markup \fontsize #-2 "又"
jsDiziDa = ^\markup \fontsize #-2 "丁"
jsDiziLiUp = ^\markup \fontsize #-2 "⤴"
jsDiziLiDown = ^\markup \fontsize #-2 "⤵"
jsDiziQizhen = ^\markup \fontsize #-2 "氣震"
jsBowDownHook = ^\markup \with-dimensions #'(0 . 1.6) #'(0 . 0.8) \postscript "0.12 setlinewidth 0 0.7 moveto 1.4 0.7 lineto 1.0 0.15 lineto stroke"
jsBowUpHook = ^\markup \with-dimensions #'(0 . 1.6) #'(0 . 0.8) \postscript "0.12 setlinewidth 1.4 0.7 moveto 0 0.7 lineto 0.4 0.15 lineto stroke"
jsBreathVBold = { \once \override BreathingSign.text = \markup \bold \sans "V" \breathe }
jsCaesura = { \once \override BreathingSign.text = \markup \musicglyph "scripts.caesura.straight" \breathe }
jsSanban = ^\markup \with-dimensions #'(0 . 1.6) #'(0 . 1.4) \postscript "0.12 setlinewidth 0 0.7 moveto 1.6 0.7 lineto 0.5 0 moveto 0.5 1.4 lineto 1.1 0 moveto 1.1 1.4 lineto stroke"
jsDiziK = ^\markup \fontsize #-2 "K"
jsDiziDuo = ^\markup \with-dimensions #'(0 . 1.6) #'(0 . 1.4) \postscript "0.12 setlinewidth 0 1.3 moveto 1.4 1.3 lineto 0 1.0 moveto 1.4 1.0 lineto 0.7 0.8 moveto 0.7 0.25 lineto 1.15 0.25 lineto stroke 1.1 0.45 moveto 1.5 0.25 lineto 1.1 0.05 lineto closepath fill"
jsDiziCircular = ^\markup \fontsize #-3 \circle "∞"
jsGzShuangtuo = ^\markup \fontsize #-2 \override #'(baseline-skip . 1.7) \center-column { "∟" "∟" }
jsStrokeLun = ^\markup \with-dimensions #'(0 . 1.3) #'(0 . 1.7) \postscript "0.12 setlinewidth 0.2 0 moveto 1.0 1.6 lineto 0.15 0.5 moveto 0.85 0.5 lineto 0.3 0.8 moveto 1.0 0.8 lineto 0.45 1.1 moveto 1.15 1.1 lineto stroke"
jsStrokeYao = ^\markup \with-dimensions #'(0 . 1.3) #'(0 . 1.7) \postscript "0.12 setlinewidth 0.2 0 moveto 1.0 1.6 lineto 0.2 0.65 moveto 0.9 0.65 lineto 0.4 0.95 moveto 1.1 0.95 lineto stroke"
jsRuanLunThree = ^\markup \with-dimensions #'(0 . 1.4) #'(0 . 1.4) \postscript "0.12 setlinewidth 0.7 0.7 translate 3 { gsave 0 0.38 translate 0.38 1 scale newpath 0 0 0.32 0 360 arc fill grestore 120 rotate } repeat"
jsRuanLunFour = ^\markup \with-dimensions #'(0 . 1.4) #'(0 . 1.4) \postscript "0.12 setlinewidth 0.7 0.7 translate 4 { gsave 0 0.38 translate 0.38 1 scale newpath 0 0 0.32 0 360 arc fill grestore 90 rotate } repeat"
jsHulusiClose = ^\markup \with-dimensions #'(0 . 1.8) #'(0 . 1.0) \postscript "0.12 setlinewidth 0.4 0.5 0.32 0 360 arc stroke 0.75 0.5 moveto 1.45 0.5 lineto stroke 1.35 0.72 moveto 1.75 0.5 lineto 1.35 0.28 lineto closepath fill"
jsXuzhiSpan = -\tweak bound-details.left.text \markup \draw-circle #0.35 #0.12 ##f \startTrillSpan
jsWavySpan = -\tweak bound-details.left.text \markup \null \startTrillSpan
jsStringOne = ^\markup \fontsize #-3 \circle "一"
jsStringTwo = ^\markup \fontsize #-3 \circle "二"
jsStringThree = ^\markup \fontsize #-3 \circle "三"
jsStringFour = ^\markup \fontsize #-3 \circle "四"
jsStringFive = ^\markup \fontsize #-3 \circle "五"
jsYangqinShuangtanlun = ^\markup \with-dimensions #'(0 . 1.0) #'(0 . 1.7) \postscript "0.12 setlinewidth 0.5 0 moveto 0.5 1.6 lineto 0.2 0.6 moveto 0.8 0.6 lineto 0.2 1.0 moveto 0.8 1.0 lineto stroke"
jsSfMidi = #(make-music 'PostEvents 'elements (list (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 20 'tweaks (list (cons 'stencil #f)))))
jsFzMidi = #(make-music 'PostEvents 'elements (list (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 20 'tweaks (list (cons 'stencil #f)))))
jsSfzMidi = #(make-music 'PostEvents 'elements (list (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 20 'tweaks (list (cons 'stencil #f)))))
jsSfzzMidi = #(make-music 'PostEvents 'elements (list (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 40 'tweaks (list (cons 'stencil #f)))))
jsFpMidi = #(make-music 'PostEvents 'elements (list (make-dynamic-script "fp") (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 20 'tweaks (list (cons 'stencil #f)))))
jsSfpMidi = #(make-music 'PostEvents 'elements (list (make-dynamic-script "sfp") (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 20 'tweaks (list (cons 'stencil #f)))))
jsSfppMidi = #(make-music 'PostEvents 'elements (list (make-dynamic-script "sfpp") (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 20 'tweaks (list (cons 'stencil #f)))))
jsRfMidi = #(make-music 'PostEvents 'elements (list (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 20 'tweaks (list (cons 'stencil #f)))))
jsRfzMidi = #(make-music 'PostEvents 'elements (list (make-music 'ArticulationEvent 'articulation-type 'accent 'midi-extra-velocity 20 'tweaks (list (cons 'stencil #f)))))
%% ---- end prelude ----

```

## 3. 匯出演算法（參考實作：`work/probes/notation/proto_export.py`、`conv.py`）

### 3.1 `Score.to_jianpu()`（v2）

```
title= / composer= / NoIndent / [OctavesBefore 若有任何倚音] / [WithStaff]
for 每個聲部 i:
    i>0: "NextPart"
    多聲部且有名稱: "instrument=" + literal(name)        # 上游每個聲部各自讀一次，不會互相影響
    "1=KEY METER[,弱起]" + (" 4=TEMPO" 只寫在 i==0)       # 速度只印一次，MIDI 的 tempo 是全曲共用（parts_tempo_once ✓）
    依 (pos - p) % bar 切小節；每小節輸出一行（若該行以 /^[A-Za-z]+=/ 開頭，就接到上一行後面：P4）
    bars_per_line：只在聲部 0 輸出 "\break"（Score 層級，全部聲部一起換行）
    有歌詞: "H: " + literal(lyrics)                     # 每個聲部各自的歌詞（parts_plain / f11 ✓）
```
弱起寫法：`pickup_token(p)`，p == 4·48/d 時輸出 `,d`，p == 1.5 倍時輸出 `,d.`。

**小節層級項目複製到每個聲部**：每個聲部輸出第 k 小節時，第一個音套用 `parts[0]` 第 k 小節第一個音的 `bar_start`，最後一個音套用其 `bar_end`。結構項目（R{ } A{ | } Segno ToCoda DS DC Fine）**每個聲部都要寫**，因為上游依各聲部自己的大括號組成 \repeat（parts_pickup_repeat、parts_ds_coda 都已驗證，各聲部 MIDI 對齊）。只印不跳的 `\textMark`／`\textEndMark`（jsCodaSign、jsSegnoSign、jsFineText、jsDS…Text）**只寫在聲部 0**，因為這些是 Score 層級，寫兩次就會印兩次（f10 第一版出現 Fine Fine）。

**每個音的字詞順序**（`note_words`）：
1. bar_start：`R{`（第 0 小節時再加 `\bar ".|:"`）；`volta.1` 輸出 `} A{`；`volta.k`（k≥2）輸出 `|`；`nav.segno` 輸出 `Segno`；只印記號（僅聲部 0）；`volta.final` 輸出 `\jsVoltaJie`
2. `3[`
3. span 起點中屬於 `pre` 的部分（`\jsOttavaUp/Down`）
4. `g[..]`
5. `pre` 類記號（slideUp/slideDown/slide=X、arp*、glis）
6. 音頭（`trem.3` 時加 `///`）
7. `post` 類記號（指法／弦序先輸出，其餘依登記表順序）→ span 終點的 post 部分（`)` `\!` `\stopTrillSpan` `\stopTextSpan`）→ span 起點的 post 部分（`(` `\<` …）
8. 延長線 `-`
9. `[..]g`
10. `~`
11. `after` 類記號（`\breathe`、`\jsBreathV`、`\jsCaesura`）
12. span 終點中屬於 after 的部分（`\jsOttavaEnd`）
13. `]`
14. bar_end：線型 `\bar "!"`／`"||"`（下一小節是反覆起點時省略：P3；最後一小節的 bar.final 也省略，因為上游會自動補 `|.`）→ `}`（反覆結束；下一小節是房子時不寫）→ `}`（volta.end）→ `ToCoda`／`DS`／`DC`／`Fine`（**有 DC 或 DS 時才寫 `Fine`，否則改寫 `\jsFineText`**：P5）→ 只印記號

跨小節的線條：漸強弱、rit/accel 延伸線、trill、slur 跨小節線都沒有問題（combo_hairpin_across_bar、combo_rit_span_across_bar ✓）。延音線跨小節沿用 v1 的作法。

### 3.2 worker 的轉換流程（在 `adapt_output` 之後依序執行）

| 步驟 | 轉換內容（字面） | 為什麼 |
|---|---|---|
| a | 在第一個 `\score {` 之前插入 **prelude**（§2 末尾全文，由登記表和 `print_variant` 產生） | 巨集只能由 app 定義，使用者原始碼只能**引用**名稱 |
| b | `fix_fine`：每個 `\volta 2 \fine \volta 1` 後面插入 ` {`，並在所屬區塊的右大括號前插入 `} ` | P2 |
| c | `midi_rewrite`：只處理 `% === BEGIN MIDI STAFF ===` 到 `% === END MIDI STAFF ===` 之間的文字：`\jsOttavaUp`→`\transposition c''`，`\jsOttavaDown`→`\transposition c`，`\jsOttavaEnd`→`\transposition c'`（不加大括號：上游可能把線條字放在三連音或後倚音的區塊裡）；`\js(Sf\|Fz\|Sfz\|Sfzz\|Rf\|Rfz\|Fp\|Sfp\|Sfpp)`→同名加 `Midi` | 8va 在 LilyPond 只影響記譜，不改發聲音高；突強類力度在 MIDI 只加力度值，不改後續音量 |
| d | 多聲部時：prelude 加入 `\layout { \context { \Score systemStartDelimiter = #'SystemStartBrace \consists Span_bar_engraver \override SpanBar.bar-extent = #'(-2 . 2) } }` | 大括號加上貫通小節線。**不需要改寫 .ly 文字**（`--brace` 那種包 StaffGroup 的作法也能編譯，但左邊會多一條系統起始線，比較醜，不採用） |
| e | 弱起而且最後一小節完整時：轉換子程序的環境變數設定 `j2ly_sloppy_bars=1`（例如 `engine.render(…, allow_full_final_bar=True)` 傳入 `worker_env`） | 上游規則 |

**validate_source（使用者原始碼）**：維持禁止 `LP:`／`LPH:`、`#(`、`#{`、`$`。不需要開放 LP 區塊：所有需要 Scheme 的東西都放進 app 自己產生的 prelude，使用者只能寫單一 `\名稱` 字。白名單＝v1 名單＋§2 末尾列出的新增名稱（**由登記表自動產生**，不要手抄）。另外新增規則：一行若以 `slide=` 或 `Fr=` 開頭就拒絕（P4）。注意 prelude 是在 validate **之後**才加入，所以不會被白名單擋下。`js*Midi` 這組 MIDI 專用版本**不列入**白名單，只有 worker 會產生它們。

### 3.3 MIDI／試聽行為（實測，全部是 LilyPond 2.26 `\unfoldRepeats` 的結果）

| 項目 | 實際結果 | 證據 |
|---|---|---|
| 反覆 R{ } | **展開**，播兩遍 | repeat_volta：25 音＝8×2+4+1+4 |
| 房子 1/2/結（3 遍） | 展開，各房依次播放 | f05：1234 5555 1234 6666 1234 1 |
| 雙向反覆 | 展開 | repeat_endstart 17 音 |
| D.C. al Fine | 有 P2 修正時正確（從頭播到 Fine）；沒有修正時整首播兩遍並出現警告 | f06 21 音；dc_al_fine_nofix 26 音 |
| D.S. al Coda | 正確：回到 Segno，到 ToCoda 跳到 Coda | f07 29 音 |
| D.S. al Fine | 有 P2 修正時正確 | ds_no_coda 24 音 |
| D.S./D.C. 段落裡的反覆 | 回頭那一遍**會再反覆一次**（LilyPond 預設，不是「senza replica」） | ds_with_inner_repeat 33 音 |
| 弱起 | 第一個音從 0 開始，長度等於弱起長度，沒有補空拍；最後一小節可以完整（sloppy） | pickup_ok、f03 |
| 多聲部 | 每個聲部一條 track，起點對齊；聲部開頭的休止會讓第一個音延後 | parts_*、f10、f11 |
| 速度 | 只寫在聲部 0 就夠了（tempo 寫在 track 0，全曲適用）；沒寫速度時是 84 | parts_tempo_once |
| 力度 ppp–fff | 依 `absolute-volume-alist` 設定音量（p→69、mp→77、mf→86、f→95、ff→101） | midi_dynamics、f01 |
| sf/fz/sfz/rf/rfz | 這一個音力度 +20（sfzz +40），**後面的音量不變** | f01：f 95 → sfz 115 → 回到 95 |
| fp/sfp/sfpp | 這一個音 +20，之後音量變成 p/p/pp | f01 |
| 漸強／漸弱 | 在兩個力度之間逐音插值 | combo_hairpin_across_bar 91→95 |
| 重音／強重音 | 力度 +20／+40 | midi_dynamics |
| 頓音（▼）／斷音 | 縮短一半（最多 0.5 秒）、+4；斷音很短、+6 | 同上 |
| ▽ | 用 portato 實作：縮短到 3/4 | 同上 |
| 延長記號、保持音、連線、滑音、glissando、裝飾音、tr、震音（含 `///` 的 `:32`）、技法文字、rit./accel. | **沒有效果**（震音只播一個長音） | midi_tremolo 5 音 |
| 倚音、後倚音 | 會播，佔用時間很短 | combo_grace |
| 8va／8vb | 有 worker 改寫時 ±12；沒有改寫時照寫的音高播放 | midi_ottava、f08 |
| 踏板 | CC64 127/0 | midi_pedal |
| 延音線 | 合併成一個音 | combo_tie_tuplet |

## 4. 驗收 fixtures（答案卷）

`work/probes/notation/out/fixtures/<name>.json`（v2 專案檔）和 `<name>.jly`（期望的 jianpu-ly 文字）。每一份都已經跑過 conv.py（就是 §3.2 的 a–e）、LilyPond 2.26：rc=0、沒有警告、PNG 已經看過，MIDI 結果如 §3.3。建造者的單元測試：`to_jianpu(load(json)) == jly`（逐字比對），整合測試再跑真實 LilyPond。

### f01_dynamics：S3 力度＋漸強弱；MIDI 69→95，sfz 115 後回到 95，fp/sfpp 後依序為 69/62
結果：rc=0，警告 0 個，MIDI 各聲部音數 [12]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":5,"marks":["dyn.p"],"spans_start":["hairpin.cresc"]},
   {"degree":6},
   {"degree":1,"octave":1},
   {"degree":2,"octave":1,"marks":["dyn.f"],"spans_end":["hairpin.cresc"]},
   {"degree":3,"octave":1,"marks":["dyn.sfz"]},
   {"degree":2,"octave":1,"marks":["dyn.fp"]},
   {"degree":1,"octave":1,"marks":["dyn.sfpp"]},
   {"degree":6,"marks":["dyn.rf"]},
   {"degree":5,"marks":["dyn.mf"],"spans_start":["hairpin.dim"]},
   {"degree":3},
   {"degree":2},
   {"degree":1,"marks":["dyn.pp"],"spans_end":["hairpin.dim"]}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4 4=80
c5 \p \< c6 c1' c2' \f \!
c3' \jsSfz c2' \jsFp c1' \jsSfpp c6 \jsRf
c5 \mf \> c3 c2 c1 \pp \!
```

### f02_pickup：S8 八分音符弱起，最後一小節 3.5 拍
結果：rc=0，警告 0 個，MIDI 各聲部音數 [12]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 24, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":5,"duration":8},
   {"degree":1},
   {"degree":2},
   {"degree":3},
   {"degree":4},
   {"degree":5,"duration":2},
   {"degree":3},
   {"degree":1},
   {"degree":2},
   {"degree":2},
   {"degree":3},
   {"degree":1,"duration":8}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4,8 4=80
q5
c1 c2 c3 c4
5 - c3 c1
c2 c2 c3 q1
```

### f03_pickup_full_last：弱起＋完整最後一小節 → worker 設 j2ly_sloppy_bars=1
結果：rc=0，警告 0 個，MIDI 各聲部音數 [6]，env {'j2ly_sloppy_bars': '1'}
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 48, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":5},
   {"degree":1},
   {"degree":2},
   {"degree":3},
   {"degree":4},
   {"degree":5,"duration":1}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4,4 4=80
c5
c1 c2 c3 c4
5 - - -
```

### f04_volta：反覆＋一房／二房；MIDI 展開 25 音
結果：rc=0，警告 0 個，MIDI 各聲部音數 [25]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":1,"bar_start":["bar.repeat_start"]},
   {"degree":2},
   {"degree":3},
   {"degree":4},
   {"degree":5},
   {"degree":6},
   {"degree":5},
   {"degree":4,"bar_end":["bar.repeat_end"]},
   {"degree":3,"bar_start":["volta.1"]},
   {"degree":2},
   {"degree":1},
   {"degree":2},
   {"degree":1,"duration":1,"bar_start":["volta.2"],"bar_end":["volta.end"]},
   {"degree":5},
   {"degree":5},
   {"degree":5},
   {"degree":5}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4 4=80
R{ \bar ".|:" c1 c2 c3 c4
c5 c6 c5 c4
} A{ c3 c2 c1 c2
| 1 - - - }
c5 c5 c5 c5
```

### f05_volta_jie：虛小節線刻意放在反覆起點前（會被省略：P3）；三遍＋第三房標「結」
結果：rc=0，警告 0 個，MIDI 各聲部音數 [25]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":3},
   {"degree":3},
   {"degree":2},
   {"degree":2,"bar_end":["bar.dashed"]},
   {"degree":1,"bar_start":["bar.repeat_start"]},
   {"degree":2},
   {"degree":3},
   {"degree":4,"bar_end":["bar.repeat_end"]},
   {"degree":5,"bar_start":["volta.1"]},
   {"degree":5},
   {"degree":5},
   {"degree":5},
   {"degree":6,"bar_start":["volta.2"]},
   {"degree":6},
   {"degree":6},
   {"degree":6},
   {"degree":1,"duration":1,"bar_start":["volta.3","volta.final"],"bar_end":["volta.end"]}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4 4=80
c3 c3 c2 c2
R{ c1 c2 c3 c4
} A{ c5 c5 c5 c5
| c6 c6 c6 c6
| \jsVoltaJie 1 - - - }
```

### f06_dc_al_fine：D.C. al Fine（需要 P2 修正）；MIDI 21 音
結果：rc=0，警告 0 個，MIDI 各聲部音數 [21]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":1},
   {"degree":2},
   {"degree":3},
   {"degree":4},
   {"degree":5},
   {"degree":6},
   {"degree":5},
   {"degree":4,"bar_end":["nav.fine"]},
   {"degree":3},
   {"degree":3},
   {"degree":2},
   {"degree":2},
   {"degree":1,"duration":1,"bar_end":["nav.dc"]}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4 4=80
c1 c2 c3 c4
c5 c6 c5 c4 Fine
c3 c3 c2 c2
1 - - - DC
```

### f07_ds_al_coda：Segno／To Coda／D.S.／Coda；MIDI 29 音
結果：rc=0，警告 0 個，MIDI 各聲部音數 [29]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":1},
   {"degree":1},
   {"degree":1},
   {"degree":1},
   {"degree":2,"bar_start":["nav.segno"]},
   {"degree":2},
   {"degree":2},
   {"degree":2},
   {"degree":3},
   {"degree":3},
   {"degree":3},
   {"degree":3,"bar_end":["nav.tocoda"]},
   {"degree":4},
   {"degree":4},
   {"degree":4},
   {"degree":4,"bar_end":["nav.ds"]},
   {"degree":5},
   {"degree":5},
   {"degree":5},
   {"degree":5},
   {"degree":6,"duration":1,"bar_end":["bar.final"]}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4 4=80
c1 c1 c1 c1
Segno c2 c2 c2 c2
c3 c3 c3 c3 ToCoda
c4 c4 c4 c4 DS
c5 c5 c5 c5
6 - - -
```

### f08_ornaments：波音（含 ♯）、複波音、tr♭、震音 1–4、8va（MIDI +12）、tr～～、琶音、延長記號
結果：rc=0，警告 0 個，MIDI 各聲部音數 [15]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":5,"marks":["orn.prall"]},
   {"degree":6,"marks":["orn.mordent_sharp"]},
   {"degree":5,"marks":["orn.prallprall"]},
   {"degree":3,"marks":["orn.trill_flat"]},
   {"degree":2,"marks":["trem.1"]},
   {"degree":2,"marks":["trem.2"]},
   {"degree":2,"marks":["trem.3"]},
   {"degree":2,"marks":["trem.4"]},
   {"degree":5,"spans_start":["line.8va"]},
   {"degree":6},
   {"degree":1,"octave":1},
   {"degree":2,"octave":1,"spans_end":["line.8va"]},
   {"degree":3,"duration":2,"spans_start":["orn.trill_span"]},
   {"degree":2,"spans_end":["orn.trill_span"]},
   {"degree":1,"marks":["arp.up","art.fermata"]}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4 4=80
c5 \prall c6 \jsMordentSharp c5 \prallprall c3 \jsTrillFlat
c2 \jsTremOne c2 \jsTremTwo c2/// c2 \jsTremFour
\jsOttavaUp c5 c6 c1' c2' \jsOttavaEnd
3 \startTrillSpan - c2 \stopTrillSpan arpUp c1 \fermata
```

### f09_erhu：同一音上的指法＋弓法＋內弦＋力度＋重音；頓音 ▼／▽、滑音、揉弦、空弦 0、倚音＋連線、撥弦、延音線、三連音＋換氣、休止符上的延長記號、顫弓
結果：rc=0，警告 0 個，MIDI 各聲部音數 [14]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "tc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":5,"marks":["finger.1","bow.down","erhu.inner","dyn.mp","art.accent"]},
   {"degree":6,"marks":["finger.2","bow.up","art.staccato"]},
   {"degree":1,"octave":1,"marks":["finger.3","slide.up","erhu.vibrato"]},
   {"degree":6,"marks":["finger.0","slide.down","erhu.outer","art.staccato_hollow"]},
   {"degree":5,"grace_before":"56","marks":["erhu.dun"],"spans_start":["line.slur"]},
   {"degree":3,"spans_end":["line.slur"],"marks":["finger.4"]},
   {"degree":2,"marks":["erhu.pizz_left"],"tie":true},
   {"degree":2,"marks":["harmonic"]},
   {"degree":5,"duration":8,"tuplet":3,"tuplet_start":true,"marks":["erhu.pao"]},
   {"degree":6,"duration":8,"tuplet":3},
   {"degree":5,"duration":8,"tuplet":3,"tuplet_end":true,"marks":["breath.v"]},
   {"degree":0,"marks":["art.fermata"]},
   {"degree":3,"marks":["erhu.trem_bow"]},
   {"degree":2,"marks":["tempo.rit"]}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
OctavesBefore
1=D 4/4 4=80
c5 Fr=1 \mp \accent \downbow \jsErhuInner c6 Fr=2 \staccato \upbow slideUp c1' Fr=3 \jsErhuVibrato slideDown c6 \jsFingerZero \jsStaccatoHollow \jsErhuOuter
g[56] c5 \jsErhuDun ( c3 Fr=4 ) c2 \jsPizzLeft ~ c2 \flageolet
3[ q5 \jsErhuPao q6 q5 \jsBreathV ] c0 \fermata c3/// c2 \jsRit
```

### f10_duet：二胡二重奏：反覆起（曲首 |:）、Coda 記號、無房反覆止、連線、♯／♭、指法、最後一小節 Fine（只印）；大括號＋貫通小節線
結果：rc=0，警告 0 個，MIDI 各聲部音數 [24, 19]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "二重奏範例", "composer": "", "key": "G", "meter": "4/4", "tempo": 66, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "tc","parts":[
 {"name":"二胡一","lyrics":"","notes":[
   {"degree":5,"duration":8,"bar_start":["bar.repeat_start"],"spans_start":["line.slur"],"marks":["finger.1"]},
   {"degree":6,"duration":8},
   {"degree":1,"octave":1,"spans_end":["line.slur"]},
   {"degree":4,"accidental":"#","marks":["finger.3"]},
   {"degree":5},
   {"degree":6,"bar_start":["nav.coda_sign"],"marks":["dyn.mf"]},
   {"degree":5,"duration":8},
   {"degree":3,"duration":8},
   {"degree":2,"duration":2,"bar_end":["bar.repeat_end"]},
   {"degree":3,"spans_start":["line.slur"]},
   {"degree":7,"accidental":"b","octave":-1,"marks":["finger.2"]},
   {"degree":6,"octave":-1,"duration":8},
   {"degree":5,"octave":-1,"duration":8,"spans_end":["line.slur"]},
   {"degree":6,"octave":-1},
   {"degree":5,"octave":-1,"duration":1,"bar_end":["nav.fine"],"marks":["art.fermata"]}
 ]},
 {"name":"二胡二","lyrics":"","notes":[
   {"degree":1,"marks":["finger.0"]},
   {"degree":1,"duration":8},
   {"degree":2,"duration":8},
   {"degree":3,"duration":2},
   {"degree":4,"marks":["dyn.mp"]},
   {"degree":3},
   {"degree":2,"duration":2},
   {"degree":1,"spans_start":["line.slur"]},
   {"degree":3,"octave":-1,"duration":8},
   {"degree":5,"octave":-1,"duration":8,"spans_end":["line.slur"]},
   {"degree":6,"octave":-1,"duration":2},
   {"degree":3,"octave":-1,"duration":1,"marks":["art.fermata"]}
 ]}
]}}
```
```text
title=二重奏範例
composer=
NoIndent
instrument=二胡一
1=G 4/4 4=66
R{ \bar ".|:" q5 Fr=1 ( q6 c1' ) c#4 Fr=3 c5
\jsCodaSign c6 \mf q5 q3 2 - }
c3 ( cb7, Fr=2 q6, q5, ) c6,
5, \fermata - - - \jsFineText
NextPart
instrument=二胡二
1=G 4/4
R{ \bar ".|:" c1 \jsFingerZero q1 q2 3 -
c4 \mp c3 2 - }
c1 ( q3, q5, ) 6, -
3, \fermata - - -
```

### f11_parts_pickup_lyrics：兩個聲部＋四分音符弱起＋各自歌詞
結果：rc=0，警告 0 個，MIDI 各聲部音數 [8, 5]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 48, "print_variant": "tc","parts":[
 {"name":"高音","lyrics":"一 二三四五 六七八","notes":[
   {"degree":5},
   {"degree":1},
   {"degree":2},
   {"degree":3},
   {"degree":3},
   {"degree":2},
   {"degree":1},
   {"degree":2}
 ]},
 {"name":"低音","lyrics":"甲乙丙丁戊","notes":[
   {"degree":0},
   {"degree":1,"duration":2},
   {"degree":5,"octave":-1,"duration":2},
   {"degree":5,"octave":-1},
   {"degree":5,"octave":-1},
   {"degree":5,"octave":-1}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
instrument=高音
1=D 4/4,4 4=80
c5
c1 c2 c3 c3
c2 c1 c2
H: 一 二三四五 六七八
NextPart
instrument=低音
1=D 4/4,4
c0
1 - 5, -
c5, c5, c5,
H: 甲乙丙丁戊
```

### f12_folk_sc：古箏／琵琶／笛子記號，簡體印字，8vb，踏板，rit. 延伸線
結果：rc=0，警告 0 個，MIDI 各聲部音數 [16]
```json
{"format":"jianpu-studio","version":2,"score":{"title": "Fixture", "composer": "", "key": "D", "meter": "4/4", "tempo": 80, "with_staff": false, "paper_size": "a4", "staff_size": 20, "bars_per_line": 0, "pickup": 0, "print_variant": "sc","parts":[
 {"name":"","lyrics":"","notes":[
   {"degree":5,"marks":["gz.tuo"]},
   {"degree":6,"marks":["gz.gou"]},
   {"degree":1,"octave":1,"marks":["stroke.yao"]},
   {"degree":6,"marks":["gz.gua_up"]},
   {"degree":5,"marks":["pipa.tiao"]},
   {"degree":3,"marks":["stroke.lun"]},
   {"degree":2,"marks":["pipa.yin"]},
   {"degree":1,"marks":["pipa.tui"]},
   {"degree":5,"marks":["dizi.tu2"],"spans_start":["line.8vb"]},
   {"degree":6,"marks":["dizi.huashe"]},
   {"degree":5,"marks":["dizi.die"]},
   {"degree":3,"marks":["breath.v_bold"],"spans_end":["line.8vb"]},
   {"degree":2,"marks":["pedal.on"],"spans_start":["tempo.rit_span"]},
   {"degree":1},
   {"degree":6,"octave":-1},
   {"degree":5,"octave":-1,"marks":["pedal.off"],"spans_end":["tempo.rit_span"]}
 ]}
]}}
```
```text
title=Fixture
composer=
NoIndent
1=D 4/4 4=80
c5 \jsGzTuo c6 \jsGzGou c1' \jsStrokeYao c6 \jsGzGuaUp
c5 \jsPipaTiao c3 \jsStrokeLun c2 \jsPipaYin c1 \jsPipaTui
\jsOttavaDown c5 \jsDiziTuTwo c6 \jsDiziHuashe c5 \jsDiziDie c3 \jsBreathVBold \jsOttavaEnd
c2 \sustainOn \jsRitSpan c1 c6, c5, \sustainOff \stopTextSpan
```


## 5. 風險、做不到的事、建議砍的範圍（依優先順序）

1. **（高）平台線升 2.26 時必須同時帶上 P1 的 Fingering 字型修正**，否則現有使用者的二胡指法和頓音會在 PDF 上無聲消失。這要加進 worker 和引擎測試：用 PDF 文字抽取確認「一」「▼」存在。我們的 QtPdf `getAllText` 抽得到 CJK 技法字，可以拿來當自動檢查。
2. **（高）民樂技法符號的印法未經老師確認。** 低信心的有 27 筆（二胡 回滑、墊指滑、壓揉、滑揉、人工泛音◇、換把、打音；古箏 劈 提 大撮 小撮 搖指 花指 按音 顫音；琵琶 彈 挑 輪 掃 拂 搖 滾 帶起；笛子 打音 歷音上下 氣震音）。琵琶輪指和古箏搖指另有中信心的 JP-Word 形狀版本（stroke.lun／stroke.yao）。建議 0.4.0 面板只開放 high/medium（含 JP-Word 已確認的 ∟、雙托、T/K、丁、又、三輪/四輪、圈一–五、散板等），低信心的保留在目錄裡但預設隱藏。目錄是資料驅動的，之後開放只需要改資料，不用重新設計。
3. **（高）字型覆蓋率**：CJK 技法字、∟ ※ ∧ ∨ ‖ ⤴ ⤵ ∞ ↗ ↘ ⌒ 走的是 serif 字型清單。macOS 這台實際用到的是 STSong 和系統替代字型，看起來沒有缺字（豆腐字），但 **Windows 和內建 Noto 字型還沒驗證**。Windows 打包測試必須把 sheet_* 全部重跑並看圖。不是 CJK 的形狀（⤴ ⤵ ∞ ‖）以後應該改用 \postscript，讓它不依賴字型。
4. **（中）多聲部小節編輯要同步所有聲部**：measures.py 需要大改（每個操作都要套到全部聲部，且是一個復原步驟）。畫布編輯第二聲部時，小節層級記號是唯讀的，必須到聲部 0 編輯。
5. **（中）導航的組合有限**：上游只支援一個 DS 或 DC、Segno…ToCoda…DS 的固定形式，Fine 和 ToCoda 不能同時用，導航項目也不能放進反覆或房子內部。其他寫法（例如反覆內的 To Coda）請改用「只印記號」系列（jsCodaSign、jsToCodaText 等，試聽不會跳）。介面要說清楚哪些記號「只印、不影響試聽」。
6. **（中）貫通小節線會穿過第一聲部的歌詞**（f11）。建議：只要非最後一個聲部有歌詞，就只加大括號、不加 Span_bar_engraver。
7. **（低）MIDI 不會實現的效果**：震音、裝飾音、延長記號、rit./accel.、滑音。D.S./D.C. 回頭那一遍內部反覆仍會重播。這些都要寫在限制文件裡；試聽不是演奏。
8. **（低）上游的限制**：琶音只對單音畫一小段波浪（我們沒有和弦）；墊指滑音、贈音是組合形式（倚音＋滑音，或後倚音），不是單一記號；不支援記號放在倚音本身上；房子最多 4。
9. **（低）`\bar ".|:"` 只在反覆從第 0 小節開始時才補**。曲中的反覆 LilyPond 本來就會印。
10. **建議砍掉或延後**：聲部中途才出現（已在 plan 的不做清單）、percent 式的小節反覆 `R4{}`、記號放在倚音上、和弦琶音、半孔與孔位圖、鑼鼓經、古箏花指與匯組方框、微分音、JP-Word 的弧形與波浪滑音變體。

## 6. 重現指令

```sh
cd work/probes/notation
../../../.venv/bin/python sheets.py        # 每組記號一頁（歌詞＝id），out/sheet_*/
../../../.venv/bin/python structure.py     # 弱起、反覆、房子、導航、聲部、MIDI、組合情境
../../../.venv/bin/python fixtures.py      # §4 答案卷 → out/fixtures/*.json|.jly + 編譯
../../../.venv/bin/python gen_tables.py    # §2 表格、白名單、prelude → out/tables.md
../../../.venv/bin/python zoom.py <case> [dpi] [上方比例]   # 高解析局部 PNG
../../../.venv/bin/python prelude.py       # 印出 prelude（JS_VARIANT=sc 輸出簡體，JS_PARTS=2 加大括號）
```
`conv.py` 會模擬 worker：`adapt_output` 和 2026-11-27 的 worker 相同，再加上 §3.2 的 a–e（可用 `--no-fine-fix`、`--no-midi-rewrite`、`--brace` 做對照）。`harness.py` 依序執行 validate（v1 與 v2 白名單）→ 子程序轉換 → LilyPond（LANG=C）→ QtPdf PNG 與文字 → SMF 解析，結果寫進 `out/results.json`、`out/fixtures.json`。環境：macOS（Apple Silicon）、.venv Python 3.12.16、PySide6 6.8.3、LilyPond 2.26.0（work/lilypond-mac）。

