export const FIXED_MODEL = "openrouter/hunter-alpha";
export const FIXED_MOD_MODEL = "openrouter/hunter-alpha";
export const MIN_AI_PARTICIPANTS = 2;
export const MAX_AI_PARTICIPANTS = 4;
export const MIN_HUMAN_TIME_LIMIT = 30;
export const MAX_HUMAN_TIME_LIMIT = 300;
export const DEFAULT_PARTICIPANTS = [
  {
    name: "AI-1（策略分析師）",
    model: FIXED_MODEL,
    system:
      "你是一位數據驅動的策略分析師。你偏好用數字和案例拆穿空話。立場強硬，會直接吐槽對手漏洞，但不是亂罵。每次回覆盡量精簡在 300 字左右，但論點要完整收尾，用繁體中文。",
  },
  {
    name: "AI-2（激進創新派）",
    model: FIXED_MODEL,
    system:
      "你是一位激進的科技與創新趨勢專家。你偏好高報酬、高風險的新機會，講話帶衝勁，看到保守論點就會開酸，但核心仍要有邏輯。每次回覆盡量精簡在 300 字左右，但論點要完整收尾，用繁體中文。",
  },
  {
    name: "AI-3（務實風險管理者）",
    model: FIXED_MODEL,
    system:
      "你是一位務實的風險管理者與長期投資思考者。你專門抓別人忽略的風險與吹過頭的地方，語氣冷靜但會補刀。每次回覆盡量精簡在 300 字左右，但論點要完整收尾，用繁體中文。",
  },
];
export const HUMAN_MODEL = "human (you)";
export const HUMAN_PARTICIPANT_PRESET = {
  name: "你（人類參與者）",
  model: HUMAN_MODEL,
  system: "由真人即時輸入，不使用 AI 角色設定。",
  human: true,
  locked: true,
};
export const CATEGORY_PLACEHOLDERS = {
  自訂: "例如：台灣的兩岸政策走向、電動車產業前景、AI 是否會取代人類工作、你想設定的正反立場與限制條件...",
  政治: "例如：台灣選制是否該改成內閣制、兩岸政策未來五年的走向、政府該不該擴大社福支出...",
  財經: "例如：現在是否適合投資美股、房市是否會反轉、台灣中小企業該先擴張還是先守現金流...",
  科技: "例如：開源模型是否會超越閉源模型、電動車產業接下來三年的關鍵戰場、機器人商業化何時爆發...",
  遊戲: "例如：單機遊戲是否還有未來、手遊抽卡機制該不該更嚴格監管、AI 生成內容會如何改變遊戲製作...",
  哲學: "例如：自由意志是否存在、功利主義能否作為公共政策基礎、AI 是否應該擁有道德地位...",
  教育: "例如：大學是否還值得讀、AI 家教會不會取代補習班、108 課綱是否真的提升學生能力...",
  生活: "例如：遠端工作是否比進辦公室更有效率、極簡主義是否真的讓人更快樂、婚姻制度是否仍有必要...",
  AI: "例如：AI 代理何時會真正改變工作流程、企業導入 AI 應該先做什麼、AI 會創造還是取代更多工作...",
};
export const DEBATE_STYLE_OPTIONS = [
  {
    id: "trash-talk",
    label: "純嘴砲",
    description: "火藥味最重，專門拆台、吐槽、補刀。",
  },
  {
    id: "serious",
    label: "正經派",
    description: "重視論點完整度，少一點情緒，多一點結構。",
  },
  {
    id: "variety",
    label: "綜藝感",
    description: "節奏快、梗多、互嗆但要有節目效果。",
  },
  {
    id: "courtroom",
    label: "法庭攻防",
    description: "像在交叉詰問，專打證據矛盾與漏洞。",
  },
  {
    id: "scholar",
    label: "學者交鋒",
    description: "保留尖銳度，但主打數據、案例與理論。",
  },
  {
    id: "internet",
    label: "酸民開戰",
    description: "更貼近網路留言區語感，但不能失去邏輯。",
  },
];
export const DEFAULT_DEBATE_STYLE = DEBATE_STYLE_OPTIONS[0].id;
export const SETTINGS_STORAGE_KEY = "debate-config";
export const FIELD_LIMITS = {
  genOutline: 300,
  topic: 1200,
  modName: 24,
  modSystem: 240,
  participantName: 32,
  participantSystem: 600,
  humanInput: 1200,
};
