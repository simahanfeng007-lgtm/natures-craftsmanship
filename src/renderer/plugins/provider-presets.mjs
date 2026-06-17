export const providerPresets = {
  openai_compatible: {
    label: "OpenAI 兼容接口",
    provider: "openai_compatible",
    baseUrl: "",
    model: "",
    thinking: {
      supported: false,
      modes: [],
      defaultDepth: "",
    },
  },
  deepseek: {
    label: "DeepSeek",
    provider: "deepseek",
    baseUrl: "https://api.deepseek.com",
    model: "deepseek-chat",
    thinking: {
      supported: true,
      modes: [
        { value: "xhigh", label: "xhigh" },
        { value: "max", label: "max" },
      ],
      defaultDepth: "xhigh",
    },
  },
  qwen: {
    label: "通义千问",
    provider: "qwen",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen-plus",
    thinking: {
      supported: true,
      modes: [
        { value: "standard", label: "标准" },
        { value: "deep", label: "深入" },
        { value: "max", label: "极深" },
      ],
      defaultDepth: "standard",
    },
  },
  zhipu: {
    label: "智谱 GLM",
    provider: "zhipu",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    model: "glm-4-plus",
    thinking: {
      supported: true,
      modes: [
        { value: "auto", label: "自动" },
      ],
      defaultDepth: "auto",
    },
  },
  moonshot: {
    label: "Moonshot",
    provider: "openai_compatible",
    baseUrl: "https://api.moonshot.cn/v1",
    model: "moonshot-v1-8k",
    thinking: {
      supported: false,
      modes: [],
      defaultDepth: "",
    },
  },
  minimax: {
    label: "MiniMax",
    provider: "minimax",
    baseUrl: "https://api.minimaxi.com/v1",
    model: "MiniMax-M3",
    thinking: {
      supported: true,
      modes: [
        { value: "auto", label: "自动" },
      ],
      defaultDepth: "auto",
    },
  },
  mimo: {
    label: "MiMo",
    provider: "mimo",
    baseUrl: "https://token-plan-cn.xiaomimimo.com/v1",
    model: "mimo-v2.5",
    thinking: {
      supported: true,
      modes: [
        { value: "auto", label: "自动" },
      ],
      defaultDepth: "auto",
    },
  },
};

const unsupportedThinking = {
  supported: false,
  modes: [],
  defaultDepth: "",
};

const providerThinkingFallbacks = {
  deepseek: providerPresets.deepseek.thinking,
  qwen: providerPresets.qwen.thinking,
  dashscope: providerPresets.qwen.thinking,
  zhipu: providerPresets.zhipu.thinking,
  glm: providerPresets.zhipu.thinking,
  minimax: providerPresets.minimax.thinking,
  mimo: providerPresets.mimo.thinking,
  openrouter: {
    supported: true,
    modes: [
      { value: "low", label: "低" },
      { value: "medium", label: "中" },
      { value: "high", label: "高" },
      { value: "xhigh", label: "xhigh" },
    ],
    defaultDepth: "high",
  },
};

export function providerOptions(selected = "openai_compatible") {
  return Object.entries(providerPresets)
    .map(([value, preset]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${preset.label}</option>`)
    .join("");
}

export function applyProviderPreset(settings, presetId) {
  const preset = providerPresets[presetId] || providerPresets.openai_compatible;
  const thinking = providerThinkingCapability(presetId, preset.provider);
  return {
    ...settings,
    modelService: presetId,
    modelProvider: preset.provider,
    modelBaseUrl: preset.baseUrl,
    modelName: preset.model,
    modelThinkingEnabled: false,
    modelThinkingDepth: thinking.defaultDepth || "",
  };
}

export function providerThinkingCapability(serviceId = "openai_compatible", providerId = "") {
  const service = String(serviceId || "").trim();
  const provider = String(providerId || "").trim().toLowerCase();
  if (provider === "openai" || provider === "openai_compatible") {
    return unsupportedThinking;
  }
  const preset = providerPresets[service];
  if (preset?.thinking?.supported) return preset.thinking;
  return providerThinkingFallbacks[provider] || unsupportedThinking;
}
