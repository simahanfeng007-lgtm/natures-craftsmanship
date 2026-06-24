import { createAppCore } from "./core/app-core.mjs";
import { plugins } from "./plugins/index.mjs";

const app = createAppCore({ runtime: window.tiangongRuntime });

for (const plugin of plugins) {
  app.registerPlugin(plugin);
}

app.boot().catch((error) => {
  console.error(error);
});
