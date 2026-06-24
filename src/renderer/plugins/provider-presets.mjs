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
  volcengine_ark: {
    label: "火山方舟",
    provider: "openai_compatible",
    baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    model: "",
    thinking: {
      supported: false,
      modes: [],
      defaultDepth: "",
    },
  },
  volcengine_agent_plan: {
    label: "火山方舟 Agent Plan",
    provider: "openai_compatible",
    baseUrl: "https://ark.cn-beijing.volces.com/api/plan/v3",
    model: "ark-code-latest",
    thinking: {
      supported: false,
      modes: [],
      defaultDepth: "",
    },
  },
  volcengine_coding_plan: {
    label: "火山方舟 Coding Plan",
    provider: "openai_compatible",
    baseUrl: "https://ark.cn-beijing.volces.com/api/coding/v3",
    model: "ark-code-latest",
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

function modelProfileForService(settings, presetId) {
  const profiles = settings && typeof settings.modelProviderProfiles === "object" && !Array.isArray(settings.modelProviderProfiles)
    ? settings.modelProviderProfiles
    : {};
  const profile = profiles[presetId];
  return profile && typeof profile === "object" && !Array.isArray(profile) ? profile : null;
}

export function applyProviderPreset(settings, presetId) {
  const preset = providerPresets[presetId] || providerPresets.openai_compatible;
  const profile = modelProfileForService(settings, presetId);
  const provider = profile?.modelProvider || preset.provider;
  const thinking = providerThinkingCapability(presetId, provider);
  return {
    ...settings,
    modelService: presetId,
    modelProvider: provider,
    modelBaseUrl: profile?.modelBaseUrl ?? preset.baseUrl,
    modelName: profile?.modelName ?? preset.model,
    modelApiKey: profile?.modelApiKey ?? "",
    modelThinkingEnabled: Boolean(profile?.modelThinkingEnabled) && Boolean(thinking.supported),
    modelThinkingDepth: profile?.modelThinkingDepth || thinking.defaultDepth || "",
    modelMultimodalInput: profile?.modelMultimodalInput || settings?.modelMultimodalInput || "auto",
    modelImageInput: profile?.modelImageInput || settings?.modelImageInput || "auto",
    modelVideoInput: profile?.modelVideoInput || settings?.modelVideoInput || "auto",
    modelAudioInput: profile?.modelAudioInput || settings?.modelAudioInput || "auto",
    webSearchProvider: profile?.webSearchProvider || settings?.webSearchProvider || "auto",
    imageGenerationMode: profile?.imageGenerationMode || settings?.imageGenerationMode || "auto",
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
