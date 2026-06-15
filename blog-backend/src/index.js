import "dotenv/config";
import express from "express";
import cors from "cors";
import { authRouter } from "./routes/auth.js";
import { postsRouter } from "./routes/posts.js";
import { errorHandler } from "./middleware/errorHandler.js";
import { requestLogger } from "./middleware/requestLogger.js";

const app = express();
const PORT = process.env.PORT || 4000;

app.use(cors({ origin: process.env.CORS_ORIGIN || "*", credentials: true }));
app.use(express.json());
app.use(requestLogger);

app.get("/api/health", (_, res) => res.json({ ok: true, service: "blog-backend" }));

app.use("/api/auth", authRouter);
app.use("/api/posts", postsRouter);

app.use(errorHandler);

app.listen(PORT, () => {
  console.log(`Blog API running at http://localhost:${PORT}`);
});
