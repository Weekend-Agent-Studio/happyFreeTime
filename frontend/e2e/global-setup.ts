import { createServer } from "vite";

export default async function globalSetup() {
  const server = await createServer({
    configFile: "./vite.config.ts",
    server: {
      host: "127.0.0.1",
      port: 4173,
      strictPort: true,
    },
  });
  await server.listen();

  return async () => {
    await server.close();
  };
}
