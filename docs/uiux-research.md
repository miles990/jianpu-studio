# 簡譜工房 UI／UX 研究與改版依據

研究日期：2026-11-12。目標使用者：用過 EOP、主要製作二胡簡譜的一般 Windows 使用者。

本次研究閱讀官方產品說明、操作手冊及維護者文件，涵蓋主要代表性產品。不是所有產品的實機比較，也不宣稱窮盡市場或證明某一設計對所有人最好。以下「採用」是本專案依使用情境做的設計判斷；實作狀態另列，避免把計畫當成已發布功能。

**現行操作以 0.3.1 為準：**樂譜設定常開、只提供圖形編輯，沒有進階語法或照片入口。下方 0.3.0／0.2.1 章節保留當時的研究與決策；其中收合設定、進階語法與原圖流程均屬歷史方案，不代表現版入口。

## 0.3.1 補充研究：設定常開與按任務分組

本輪只補查三個代表產品的官方操作資料，回答「常用工具如何分組、目前在輸入還是修改是否清楚」；不重新做全市場調查，也不以其他產品的完整功能數量作本版承諾。

| 來源 | 查核到的具體操作 | 本專案採用與使用情境 |
| --- | --- | --- |
| [MuseScore Studio 介面手冊](https://handbook.musescore.org/en_gb/navigation/the-user-interface) | 音符輸入工具列集中時值、升降、常用演奏記號與連音群；側邊另分 Palettes、Properties、Instruments，中央譜面負責選取與編輯。 | 將「音符輸入／屬性」「小節操作」「演奏記號」分成具名區域。選音改時值時不需要從小節刪除與列印按鈕間尋找。不是照搬所有側欄或多聲部功能。 |
| [Dorico Pro 6.2 Notes toolbox](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/write_mode/write_mode_notes_toolbox_r.html?contentId=obD4FWH1LDmCvsCA7zGDtg)及[Notes panel](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/write_mode/write_mode_panels/write_mode_notes_panel_r.html) | 工具箱把開始輸入、插入、附點、休止及連音群各自列出；Notes panel 又依時值、升降／連線、演奏記號分區。相同附點工具在輸入時設定新音，在非輸入時修改既有音。 | 新增、修改、填寫三種模式直接顯示，標出正在使用的模式。音高、時值、八度、升降、附點靠在一起；使用者可先確認改「下一音」還是「選取音」。本程式的三種模式不等於 Dorico 的完整工具集合。 |
| [EOP 官方附點輸入教學](https://www.everyonepiano.cn/Book-327.html) | 教學明確要求切到編輯模式，再點軟體上方附點符號；也提供鍵盤入口。 | 保留用過 EOP 的模式與鍵盤習慣，但讓「修改音符」及「附點」直接可見。滑鼠使用者可在同一音符區完成，不需先找下拉選單。 |

0.3.1 的版面取捨依使用者最新要求：曲名、來源、調號、拍號、速度常開，取消收合開關；數字與屬性一組、小節工具一組、三種記號各有直接入口。設定常開不是上述競品共同的規定，而是讓這位使用者新建樂譜後立即找到全曲設定。以區塊邊界、文字標籤與留白區分用途，並在小視窗驗證譜面仍有可閱讀空間，避免只把更多控件擠在同一列。

進階原始碼 UI 依使用者要求移除，沒有視覺工具的功能須列為未支援，不能再用「切到進階語法」作一般人操作解法。舊原始碼專案與超出視覺模型的 MusicXML 必須明確提示並保護原檔，不能悄悄只留下部分內容。

驗收案例：新建後直接改曲名與拍號；點音符後選修改並改時值；選一段音符直接開連線／反覆；點小節號後清空、刪除並復原。還要確認未支援的舊檔與 MusicXML 不會替換目前樂譜或遺失內容。實作仍須通過操作、回歸與 Windows 發布驗證，不能只以版面截圖判定完成。

## 0.3.0 補充研究：小節操作與同譜面編輯

研究日期：2026-11-12。這次針對使用者實際提出的兩個問題補查：如何新增、清空、刪除小節，以及能否直接在閱讀的譜面上編輯。沿用下方 EOP、JP-Word、MuseScore、Dorico 的基礎研究，補讀 Flat、Noteflight、Soundslice 的官方資料；不是全市場盤點，也未對這些產品逐一做實機操作或效能比較。

### 來源與具體比較

| 產品 | 小節與音符操作的官方依據 | 閱讀、編輯與列印 | 對本專案的啟示 |
| --- | --- | --- | --- |
| Flat | [新增／移除小節](https://help.flat.io/en/music-notation-software/addmeasure/)可在選取之前、之後插入，刪除單一或範圍小節，譜尾另有加減入口；[時值變更模式](https://help.flat.io/en/music-notation-software/change-duration-insert/)明確區分覆寫與推動後續音樂。 | [列印／匯出](https://help.flat.io/en/music-notation-software/print-export/)在編輯器與閱讀器有一致入口，列印可以另調休止符合併等選項。 | 小節工具要可見、位置明確；把「寫進空位」與「插入後推」的結果說清楚。畫面可直接編輯，不代表所有列印選項必須即時呈現在同一畫面。 |
| Noteflight | [官方操作文章](https://notes.noteflight.com/best-practices/)說明刪除音符會換成同時值休止符，重複整小節也能增加小節；這是 2014／2018 年資料，只作為既有操作模型的證據。官方現行[使用手冊入口](https://support.noteflight.com/hc/en-us/articles/360020205792-User-Guide)可讀，但連入 guide 後要求 JavaScript，本次未核實現版批次增刪小節的按鈕與全部細節。 | [官方購買樂譜互動說明](https://support.noteflight.com/hc/en-us/articles/360035344372-Interacting-with-Purchased-Music)區分 Page View、Scroll View，閱讀設定可反映到 PDF；該頁是購買譜的閱讀流程，不能當成整個編輯器的功能清單。 | 保留拍數的「清空」符合既有記譜習慣；不從社群功能請求反推現版支援或不支援。 |
| Soundslice | [小節操作](https://www.soundslice.com/help/en/creating/notations/95/bars/)把前插、後插、刪小節放在 Bar 區；[休止符說明](https://www.soundslice.com/help/en/creating/notations/318/rests/)區分保留原時值改成休止，以及真正刪除休止符。其編輯器容許暫時拍數不完整，並非所有產品都自動維持完整小節。 | [編輯／閱讀切換](https://www.soundslice.com/help/en/creating/basics/88/exiting-the-editor/)沿用同一譜面，閱讀時隱藏編輯工具並改變點擊意義；[列印](https://www.soundslice.com/help/en/player/basic/142/printing/)另外產生 PDF。[掃描校對](https://www.soundslice.com/help/en/creating/pdf-import/346/editing-scans/)可開關原圖對照。 | 單一可閱讀、可選音符的主譜面適合日常寫譜；正式 PDF 與照片對照按任務開啟，不必永久佔半個畫面。 |
| MuseScore Studio | 補讀[增刪小節](https://handbook.musescore.org/en_gb/basics/adding-and-removing-measures)：可指定小節數與插入位置，從譜面右鍵或屬性區進入；[音符編輯](https://handbook.musescore.org/en_gb/basics/editing-notes-and-rests)把音符刪除成休止，另有移除範圍／小節的操作。 | 延續原研究：直接選譜面元素，再用工具修改；不必在音符卡片與另一張譜間往返。 | 「清空內容」與「刪除小節」必須使用不同名稱；一次加多小節比重複按十幾次更適合先搭曲式。 |
| Dorico Pro 6.2 | [刪除音符](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/write_mode/write_mode_arranging_tools/write_mode_deleting_items_t.html)在未開插入模式時保留休止；[刪除小節／拍](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/notation_reference/notation_reference_bars/notation_reference_bars_deleting_t.html)另外處理樂曲時間範圍。 | [切換視圖](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/user_interface/user_interface_view_type_changing_t.html)允許在 Write mode 切換頁面與連續譜面。 | 「同一樂譜可有多種呈現」與「必須固定並排兩份譜」是不同設計。編輯操作應可預期，不悄悄改動後段時間位置。 |
| EOP、JP-Word、Frescobaldi | 重用下方已查核的官方來源：前兩者提供簡譜使用者熟悉的輸入／修改與所見即所得觀念；Frescobaldi 是 LilyPond 文字加 Music View 的相關對照。 | Frescobaldi 的 PDF 點擊回原始碼適合文字編輯工作流，不能直接等同一般使用者期待的「點簡譜音符就修改」。 | 延續簡譜鍵盤習慣，將可讀譜面直接連到既有音符資料；不把高品質 PDF 預覽本身當成完成視覺編輯。 |

### 本專案採用的方向與邊界

以下是本專案依上述證據及使用者回饋做的設計決策，不是競品一致採用的唯一方式；完成項目另以版本狀態與驗證紀錄為準。

1. **預設一張可以讀、也可以點選修改的主譜面。** 曲名、調號、拍號、小節線、節奏與二胡記號應足以連續閱讀；選取、輸入提示要放在同一區域。依使用者「PDF 只在列印與 PDF 輸出時需要」的要求，主畫面以編輯樂譜為主；列印預覽及 PDF 匯出放在匯出／列印選單，不自動建立 PDF，也不以固定並排 PDF 作為日常閱讀方式。試聽獨立準備 MIDI。其後使用者再要求先移除照片功能 UI，因此本版也移除原圖對照與辨識入口；競品照片流程只保留作研究背景。
2. **小節是一個可直接操作的單位。** 點小節號選取整小節，顯示目前範圍，新增可指定數量及「選取前／後／曲尾」。清空保留小節時間，刪除才讓後面小節前移；複製小節則插入完整副本。不要讓使用者靠猜測刪除鍵究竟會做哪種事情。
3. **在空白小節內填音符，不應把後面原有小節推走。** 以明確的休止符空位承載拍數；填寫消耗可用休止時值，不足時提示，與原本自由插入音符的動作分清楚。此項屬本產品選擇，不能宣稱所有競品都如此。
4. **紙面可信度與編輯速度都要保留。** 主譜面不承諾與 LilyPond PDF 逐像素或分頁完全一致；列印前按需產生正式 PDF 預覽，直接匯出也會準備最新內容；舊排版不得冒充最新內容。多聲部與進階語法也不得假裝已轉成完整視覺編輯。
5. **小節操作應可復原，且不遺留壞記號。** 插入、清空、刪除、複製要還原選取與內容；跨範圍的連弓、延音、反覆必須檢查。空譜、首尾小節、不滿小節、非連續選取與跨小節音符均須有明確行為。

建議驗收以「先加 8 小節 → 填第 2 小節 → 第 3 小節仍在原拍點 → 清空第 2 小節 → 刪除第 2 小節 → 復原」作為連續操作範例，再比較主譜面與真正 PDF 的音樂內容。外觀截圖不能代替小節拍數、記號與 MIDI 的驗證。

## 常用簡譜記號清單與語義查核

補查日期：2026-11-12。這份清單以一般單旋律與二胡教材常見需求為主，用來檢查是否有可選取、可修改、可保存的操作；不是「所有簡譜記號」的完整標準，也不把排譜引擎能畫出的符號一律列為本程式已支援。

### 官方資料提供的分類

| 類別 | 代表項目 | 依據與本專案判斷 |
| --- | --- | --- |
| 音高與節奏 | 0–7、八度點、升降還原、減時線、附點、長音橫線、三連音 | [jianpu-ly 官方語法](https://github.com/ssb22/jianpu-ly)列出數字、八度、時值及連音群；[LilyPond 節奏文件](https://lilypond.org/doc/v2.24/Documentation/notation/writing-rhythms)區分一般時值與比例時值。三連音需要改變實際拍長，不能只畫數字 3。 |
| 連線與延長 | 連音／圓滑線、二胡連弓、同音延音線、延長記號 | [LilyPond ties/slurs](https://lilypond.org/doc/v2.24/Documentation/learning/ties-and-slurs)明確區分同音相連與演奏連貫；[演奏記號文件](https://lilypond.org/doc/v2.24/Documentation/notation/expressive-marks-attached-to-notes)另列 fermata。本專案要使用不同名稱，避免把三種符號都叫「連音」或「延音」。 |
| 力度與表情 | ppp–fff、sfz、漸強／漸弱線、重音、保持音、跳音、換氣 | [LilyPond dynamics](https://lilypond.org/doc/v2.24/Documentation/notation/expressive-marks-attached-to-notes#dynamics)區分固定力度與跨音符的漸強弱；每條漸強弱線必須有起點與終點。GUI 不能只存起始字樣。 |
| 裝飾與二胡奏法 | 顫音、波音、迴音、泛音、指法、推拉弓、上下滑音、倚音 | jianpu-ly 官方語法涵蓋指法、滑音、倚音及部分樂器符號；LilyPond 可排許多演奏記號。功能狀態仍以本程式資料模型、視覺畫布與輸出驗證為準，不承諾所有奏法都能由 MIDI 真實重現。 |
| 樂曲結構 | 調號、拍號、速度、小節線、反覆、一二房、D.C.／D.S.、Fine／Coda、弱起、多聲部 | [EOP 現行手冊索引](https://www.everyonepiano.cn/Book-286.html)把小節、倚音、三連音、圓滑線、聲部與反覆列為不同操作；[反覆教學](https://www.everyonepiano.cn/Book-319.html)區分反覆小節線、一二房與跳轉。普通反覆不能代表已完成一二房或所有導航記號。 |

EOP 的[三連音教學](https://www.everyonepiano.cn/Book-296.html)採「選總時長後拆分」。本專案最後採相同方向：選一個未附點音符或休止符，拆成三等份，總拍數不變；再逐音修改音高。這比先輸入三音再壓縮時間更適合保留後段小節位置，但不代表所有 EOP 操作或連音群都相容。其[連線教學](https://www.everyonepiano.cn/Book-297.html)也分開處理同音連線與圓滑線。EOP 官網的[更新紀錄](https://www.everyonepiano.cn/update-7.html)已有 2026-10-14 條目，與產品介紹頁顯示版本不同；本研究採具體手冊操作，不用介紹頁版本號判定全產品能力。

### 0.3.0 實作邊界

- 0.2.1 已有數字、基本時值、附點、八度、升降、長音橫線、固定力度、常見演奏記號、二胡指法／弓法／滑音、連弓線、同音延音與一般反覆的視覺操作。
- 0.3.0 補入三連音、漸強弱、前／後倚音、還原選項及額外力度／演奏記號。三連音從一個未附點的二分至十六分音符拆分，合計拍數不變；生成的三音為四分至三十二分樣式，以 3:2 演奏，不跨小節。漸強弱選取至少兩個連續音符，不能重疊；倚音依附主音、不另加小節拍數。
- 在 0.3.0 當時，一二房、弱起及多聲部只能透過進階語法處理；0.3.1 已移除該入口，這些項目目前沒有圖形工具可用。還原選項代表取消原升降，正式排譜在需要時顯示還原號，不等於能強制每個音符都印提示性還原號。
- 字面版本號、視覺畫面、序列化保存與真正 PDF／MIDI 都需查核。正式發布須通過主線與 Windows 打包、安裝及升級關卡；下載狀態以[官方 Releases](https://github.com/miles990/jianpu-studio/releases/latest)為準。功能矩陣見[使用手冊](user-guide.md)。

## 0.2.1 既有研究：競品與可採用的做法

| 類別／產品 | 查核來源與觀察 | 本專案取捨 |
| --- | --- | --- |
| EOP 簡譜大師 | [官方介紹](https://www.everyonepiano.cn/html/jianpu.html)、[操作示範與官方回覆](https://www.everyonepiano.cn/Video-2584.html)、[譜面布局教學](https://www.everyonepiano.cn/Video-4588-%E5%A6%82%E4%BD%95%E8%B0%83%E6%95%B4%E8%B0%B1%E9%9D%A2%E5%B8%83%E5%B1%80-EOP%E7%AE%80%E8%B0%B1%E5%A4%A7%E5%B8%88%E4%BD%BF%E7%94%A8%E6%95%99%E7%A8%8B.html)。數字輸入、輸入／修改模式；官方明確說明空白鍵切換模式。 | 以熟悉操作為基準。常用工具靠近譜面；空白切換新增／修改，試聽使用獨立按鈕。相容的是明確列出的操作，不代表 EOPN 格式或所有快捷鍵相容。 |
| JP-Word | [官方介紹](https://www.happyeo.com/intro_jpw.htm)、[作者的使用手冊](https://happyeo.gitbooks.io/jp-word-manual/content/01/00.html)。強調如打字般輸入及所見即所得。官方介紹頁直接抓取曾逾時，另以官方手冊核對。 | 減少輸入時反覆切換工具。顯示下一個音符會插在哪裡；讓曲名、調號、拍號與音符操作有固定位置。 |
| MuseScore Studio | [選取元素](https://handbook.musescore.org/en_gb/basics/selecting-elements)、[介面結構](https://handbook.musescore.org/en_gb/navigation/the-user-interface)、[輸入音符](https://handbook.musescore.org/en_gb/basics/entering-notes-and-rests)。區分單選、多選與範圍選取，選取狀態清楚。 | Shift 範圍選取、Ctrl 個別加選、整段複製／刪除；選取幾個音符、修改哪個屬性要明確可見。 |
| Dorico | [插入模式](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/write_mode/write_mode_insert_mode/write_mode_insert_mode_c.html)、[閱讀視圖切換](https://www.steinberg.help/r/dorico-pro/6.2/en/dorico/topics/user_interface/user_interface_view_type_changing_t.html)。插入行為會影響後續音樂位置，編寫與頁面呈現有不同目的。 | 明確選擇插前、插後或曲尾；編輯畫布與正式 PDF 各自清楚標示，避免將非正式畫布當成列印成果。 |
| Sibelius | [Panorama 官方說明](https://secure.sibelius.com/products/sibelius/5/panorama.html)。連續音樂視圖用於減少創作時頁面干擾；這是設計概念來源，不當作現版完整功能評測。 | 編輯時以樂句與小節閱讀，列印時交由 LilyPond 做正式頁面排版。 |
| Flat | [複製／剪下／貼上](https://help.flat.io/en/music-notation-software/paste/)、[工具列重整](https://flat.io/changelog/2026-03-26-a-new-editor-toolbar-on-the-web)。選取與插入位置清楚，符號與版面設定分組。 | 讓複製與重複分成不同動作，使用系統剪貼簿；常用音符工具固定，較少用的紙張設定另開面板。 |
| Noteflight | [編輯器功能](https://support.noteflight.com/hc/en-us/articles/360020883831-Editor-Features)、[快捷鍵](https://support.noteflight.com/hc/en-us/articles/360020463271-Keyboard-Shortcuts)。提供可查找的操作說明。 | 按鈕提示與一頁快捷鍵說明，讓新手能用滑鼠，熟手能逐步學鍵盤。 |
| Frescobaldi | [官方功能](https://www.frescobaldi.org/)。LilyPond 文字編輯、Music View、Point & Click、MIDI 與節奏處理。 | 0.2.1 當時保留進階語法入口與 LilyPond 排版、優先視覺編輯；0.3.1 已移除語法入口。PDF 點擊回原始碼從未列為本程式已完成能力。 |
| Soundslice | [掃描譜校對](https://www.soundslice.com/help/en/creating/pdf-import/346/editing-scans/)。辨識後在編輯器保留原始掃描。 | 原圖與可編輯結果可同時參照，提供放大，不把辨識完成視為校對完成。 |
| ScanScore | [官方 FAQ](https://scan-score.com/en/faq/)、[官方示範](https://scan-score.com/en/introducing-scanscore/)。掃描、修改、轉調、輸出，原圖可對照。 | 以「整理照片 → 辨識 → 校對 → 保存」為完整流程，錯誤要指出可採取的動作。 |
| PhotoScore | [原廠介紹](https://www.neuratron.com/photoscore.htm)。提供印刷及手寫音樂辨識，屬不同引擎能力範圍。 | 不將競品的手寫能力當成本專案已具備；目前 Audiveris 流程仍以印刷五線譜為主。 |
| Audiveris | [官方使用者編輯文件](https://audiveris.github.io/audiveris/_pages/guides/ui/README/)。文件明確指出完全無誤辨識仍不容易，提供符號與關聯校正。 | 保留詳細診斷、MusicXML 與進階校對入口。針對明確的低線距錯誤做有限重試，避免無限制反覆跑辨識。 |
| Dolce／jpeditor | [維護者專案說明](https://github.com/lodebar2026/dolce)。文字與譜面同步、格式轉換、OMR 的方向值得參考；本次未實機驗證其準確度與效能。 | 可視化與開放格式值得保留；不據此承諾完整格式相容或相同辨識品質。 |

另確認 [Finale 已停止開發與銷售](https://www.makemusic.com/press-room/press-releases-2024/makemusic-sunsets-finale/)，不作為新使用者的主要遷移目標。

## 改版前觀察到的具體問題

1. 品牌側欄與標語佔據較多空間。Windows 150% 縮放下，有效可用空間更少。
2. 音符大卡片按固定寬度換行，視覺上不容易辨認完整小節與樂句。
3. 只能單選；「複製」實際上是立即重複一個音符，與一般剪貼簿習慣不同。
4. 插入位置依選取音符決定，缺少明確的模式與游標提示。
5. 修改音符需再按套用；多音符修改沒有一致的操作。
6. 背景排版期間整個編輯區被鎖住，輸入容易被中斷。
7. 每次修改重建全部清單項目，復原歷史重複序列化整份樂譜。
8. 照片錯誤先呈現 INFO 紀錄，真正原因不易閱讀。
9. 小圖片僅寫入 300 DPI 標籤不會增加實際譜線像素；需要根據辨識引擎量測做針對性處理。
10. 專業二胡記號可由上游排版，但目前視覺工具不足；必須區分排版能力與使用者可直接操作的能力。

## 0.2.1 選定的工作區

- 頂部：新建、開啟、復原／重做、儲存、匯出。品牌只保留小標題。
- 譜面上方：曲名、調號、拍號、速度；新增／修改模式、插入位置、常用音符與時值。
- 中央：按小節組織的譜面，可縮放、捲動、跳到小節、選取一段音符。
- 選取區：選取數量與位置；只修改使用者改動的屬性，保留其他音符差異。
- 二胡記號：按需要展開的弓法、指法、滑音、力度與連線工具。
- 右側：正式 PDF、原圖校對、作業訊息。可以切換專心編輯或閱讀預覽。
- 紙張設定：A4／Letter、譜字大小與每行小節數；正式輸出以 LilyPond 實際結果為準。

以 EOP 的熟悉程度降低學習成本，同時採用清楚的選取、多音符操作與可收合工作區。這是本專案的適配方案，不照搬某個產品的全部視覺外觀或功能密度。

## 效能與驗證方式

舊版基準：2026-11-12、Linux 開發環境、Qt offscreen、停用外部排版；以 1,000／10,000 個重複四分音符，修改中間音符並處理 Qt 事件，連做 5 次。中位數約 41.3／431.6 毫秒。這是開發環境基準，不是使用者 i3 電腦的承諾，也不是複雜樂譜全面測試。

0.2.0 已實作不可變音符與共享快照、可見範圍繪製、快取幾何位置、合併連續預覽要求、辨識與排版在背景執行。參考 [Qt Model/View 文件](https://doc.qt.io/qtforpython-6.11/overviews/qtwidgets-model-view-programming.html)，仍需以本程式實測確認效果。

0.2.0 工作區改版當時在相同 Linux／Qt offscreen 環境、停用外部排版，以 1,000／10,000 個四分音符修改中間音符並處理 Qt 事件，5 次中位數約 7.89／56.66 毫秒。測量腳本為 `scripts/benchmark_editor.py`，[原始紀錄](benchmarks/editor-linux-2026-11-12.json) 註明當時是開發中的工作樹；這是特定編輯操作的比較，不能推論照片辨識、LilyPond 排版或所有硬體都有同樣改善。

0.3.0 整合工作樹另於 2026-11-12 使用同一腳本實測：Linux x86_64、Python 3.12.14、Qt 6.8.3 offscreen、1380×850 視窗、100% 縮放，停用外部排版；1,000／10,000 音符各測 5 次，修改中間音高並處理 Qt 事件的中位數為 21.89／107.95 毫秒。這是加入新版記號與檢查後的獨立測量，不能沿用前段 0.2.0 數字；也不是 Windows 或 i3 電腦的效能承諾。[0.3.0 原始紀錄](benchmarks/editor-v0.3.0-linux-2026-11-12.json)保留環境、逐次數值及當時工作樹狀態。

驗收包括：範圍選取、複製貼上、撤銷重做、混合屬性、節奏錯誤定位、未儲存保護、舊檔相容、中文路徑、不同視窗與顯示縮放、真正 PDF／MIDI 與 Windows 安裝／更新。

## 版本狀態

- **0.3.1 本版範圍**：常駐樂譜設定、音符／小節工具分組、三種輸入模式與三類記號直接入口；移除文字語法 UI 及原始碼開啟／匯出。未支援的舊文字專案與 MusicXML 明確拒絕，不覆蓋目前樂譜或原檔。正式套件以 [0.3.1 發布頁](https://github.com/miles990/jianpu-studio/releases/tag/v0.3.1)為準。
- **0.3.0 已發布範圍**：單譜面編輯預設、收合樂曲設定、PDF 僅於列印／輸出時產生、獨立 MIDI 試聽、小節前後／曲尾增刪與批次操作、清空及重複、保留後段拍點的填寫模式；補入三連音、漸強弱、倚音等記號操作。照片辨識及原圖對照入口依使用者要求暫移除。正式 PDF 仍唯讀，主譜面不保證與 PDF 分頁一致；實作與驗收不能據此推論完整多聲部或 EOPN 相容。
- **0.1.2 已發布**：低解析度自適應重試、中文診斷與紀錄複製、照片預覽減負、小視窗可捲動、使用者版 README。
- **0.2.1 更新功能**：程式內檢查、下載、SHA-256 校驗與原位置升級，保留使用者設定與樂譜。
- **0.2.1 工作區**：以上小節工作區與常用二胡工具；已完成真實 Qt 操作、1000×630／1380×850 視窗、100%／150%／200% 縮放、二胡記號 PDF／MIDI 與 A4／Letter 排版驗證。Windows 發布需通過 CI 安裝與升級關卡。
- **仍需後續評估**：完整 EOPN 相容、多聲部圖形編輯、一二房／弱起等完整視覺操作、其他連音群、進階弓法變體、所有教材記號變體。上游支援不代表目前 GUI 已支援。
