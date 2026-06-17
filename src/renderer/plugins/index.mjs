import { brandBlockPlugin } from "./brand-block.mjs";
import { conversationPanelPlugin } from "./conversation-panel.mjs";
import { executePanelPlugin } from "./execute-panel.mjs";
import { historyBlockPlugin } from "./history-block.mjs";
import { inspectorPanelPlugin } from "./inspector-panel.mjs";
import { knowledgePanelPlugin } from "./knowledge-panel.mjs";
import { lifecyclePanelPlugin } from "./lifecycle-panel.mjs";
import { lifecycleSideBlockPlugin } from "./lifecycle-side-block.mjs";
import { modeBlockPlugin } from "./mode-block.mjs";
import { navRailPlugin } from "./nav-rail.mjs";
import { personaPanelPlugin } from "./persona-panel.mjs";
import { personaSideBlockPlugin } from "./persona-side-block.mjs";
import { runtimeStatusBlockPlugin } from "./runtime-status-block.mjs";
import { settingsPanelPlugin } from "./settings-panel.mjs";
import { skillsPanelPlugin } from "./skills-panel.mjs";
import { skillsSideBlockPlugin } from "./skills-side-block.mjs";
import { workspaceBlockPlugin } from "./workspace-block.mjs";

export const plugins = [
  navRailPlugin,
  brandBlockPlugin,
  workspaceBlockPlugin,
  lifecycleSideBlockPlugin,
  personaSideBlockPlugin,
  skillsSideBlockPlugin,
  modeBlockPlugin,
  runtimeStatusBlockPlugin,
  historyBlockPlugin,
  conversationPanelPlugin,
  executePanelPlugin,
  knowledgePanelPlugin,
  skillsPanelPlugin,
  personaPanelPlugin,
  lifecyclePanelPlugin,
  settingsPanelPlugin,
  inspectorPanelPlugin
];
