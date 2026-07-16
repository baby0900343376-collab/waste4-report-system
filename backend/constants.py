"""
Module 0：主檔資料

環境部「廢四機聯單」申報系統認可的回收項目／回收型態／品牌選項。
這份清單是從官方匯入範例檔（廢四機聯單匯入範例說明_聯單申報_.ods）整理出來的，
OCR 辨識結果、人工修正表單、CSV 匯出前的驗證，都共用這份清單，
避免任何一處把不存在的選項寫進申報 CSV。
"""

ITEM_TYPES: dict[str, list[str]] = {
    "電視機": ["CRT電視", "液晶電視", "內投影電視"],
    "電冰箱": ["單門冰箱", "雙門冰箱", "多門冰箱"],
    "洗衣機": ["單槽洗衣機", "雙槽洗衣機", "滾筒洗衣機"],
    "冷、暖氣機": ["窗型冷氣", "分離式冷氣", "其他冷氣"],
}

BRANDS: dict[str, list[str]] = {
    "電視機": [
        "AOC 艾德蒙", "BENQ 明基", "CHIMEI 奇美", "CHUANPO 銓寶", "ESONIC 弘映",
        "FRIGIDAIRE 富及第", "GIBSON 吉普生", "GOLDSTAR 金星", "HAIER 海爾", "HERAN 禾聯",
        "INFOCUS 富可視", "JVC 傑偉世", "KOLIN 歌林", "LG 樂金", "MITSUBISHI 三菱重工",
        "NEOKA 新禾", "PANASONIC 松下", "PHILIPS 飛利浦", "PROTON 普騰", "SAMPO 聲寶",
        "SAMSUNG 三星", "SANSUI 山水", "SANYO 三洋", "SHARP 夏普", "SONY 新力",
        "SOWA 首華", "SYNCO 新格", "TATUNG 大同", "TECO 東元", "TOSHIBA 東芝",
        "VIEWSONDIC 優派", "VIZIO 瑞軒", "WESTINGHOUSE 西屋", "WHIRLPOOL 惠而浦",
        "ZINWILL 兆赫", "其他",
    ],
    "電冰箱": [
        "AMADUS 阿瑪迪斯", "AOC 艾德蒙", "BOSCH 博世", "DAEWOO 大宇", "FRIGIDAIRE 富及第",
        "FUJITSU 富士通", "GE 奇異", "GIBSON 吉普生", "GOLDSTAR 金星", "GORENJE 歌蘭尼",
        "HERAN 禾聯", "HITACHI 日立", "JANGPON 瑞寶", "KENMORE KENMORE", "KOLIN 歌林",
        "LG 樂金", "MITSUBISHI 三菱重工", "NATIONAL 國際", "NEOKA 新禾", "NEWAVE 棱威福",
        "PANASONIC 松下", "PHILIPS 飛利浦", "PROTON 普騰", "SAMPO 聲寶", "SAMSUNG 三星",
        "SANYO 三洋", "SHARP 夏普", "SONY 新力", "SOWA 首華", "SYNCO 新格",
        "TACICO 大矽谷", "TATUNG 大同", "TECO 東元", "TFC 旭光", "TOSHIBA 東芝",
        "WESTINGHOUSE 西屋", "WHIRLPOOL 惠而浦", "中興資訊家", "其他",
    ],
    "洗衣機": [
        "BOSCH 博世", "FRIGIDAIRE 富及第", "FUJITSU 富士通", "GE 奇異", "GIBSON 吉普生",
        "GOLDSTAR 金星", "HAIER 海爾", "HERAN 禾聯", "HITACHI 日立", "KOLIN 歌林",
        "LG 樂金", "MITSUBISHI 三菱重工", "NATIONAL 國際", "NEOKA 新禾", "PANASONIC 松下",
        "PROTON 普騰", "SAMPO 聲寶", "SAMSUNG 三星", "SANYO 三洋", "SHARP 夏普",
        "SUPA FINE 勳風", "SYNCO 新格", "TATUNG 大同", "TECO 東元", "TOSHIBA 東芝",
        "WESTINGHOUSE 西屋", "WHIRLPOOL 惠而浦", "ZANWA 晶華", "其他",
    ],
    "冷、暖氣機": [
        "AMADUS 阿瑪迪斯", "AOC 艾德蒙", "APTON 艾普頓", "BESTECH 金寶島", "BLUESKY 藍天",
        "CARRIER 開利", "CORONA 可樂娜", "CROWN 王冠", "DAIKIN 大金", "FRIGIDAIRE 富及第",
        "FROST 冰點", "FUJITSU 富士通", "GE 奇異", "GIBSON 吉普生", "GLEER 恪力",
        "GOLDSTAR 金星", "HAWRIN 華菱", "HERAN 禾聯", "HITACHI 日立", "IMARFLEX 伊瑪",
        "JANGPON 瑞寶", "KOLIN 歌林", "LG 樂金", "MAXE 萬士益", "MEDIA 美的",
        "MITSUBA 三葉", "MITSUBISHI 三菱重工", "NATIONAL 國際", "NEOKA 新禾", "NORM 新典",
        "PANASONIC 松下", "PRINCE 王子", "PROTON 普騰", "RENFOSS 良峰", "SAMPO 聲寶",
        "SAMSUNG 三星", "SANYO 三洋", "SAPORO 莎普羅", "SHARP 夏普", "SONKOR 松格",
        "SOWA 首華", "SUMMER 松林夏", "SYNCO 新格", "TAIITSU 太一", "TATUNG 大同",
        "TECO 東元", "TFC 旭光", "TOPPING 國品", "TOSHIBA 東芝", "WESTINGHOUSE 西屋",
        "WHIRLPOOL 惠而浦", "中興資訊家", "其他",
    ],
}

VALID_ITEMS = list(ITEM_TYPES.keys())

# 申報狀態機（簡化版）：need_review（尚未匯出，不管有沒有人看過/改過） -> exported（已匯出）
# 原本有個中間的 confirmed 狀態，使用者確認不需要這道區分，拿掉了。
STATUS_NEED_REVIEW = "need_review"
STATUS_EXPORTED = "exported"
ALL_STATUSES = [STATUS_NEED_REVIEW, STATUS_EXPORTED]
