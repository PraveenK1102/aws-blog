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

// Static uploads (local; replace with S3 URL later)
app.use("/uploads", express.static(process.env.UPLOAD_DIR || "uploads"));

app.get("/health", (_, res) => res.json({ ok: true, service: "blog-backend" }));

app.use("/auth", authRouter);
app.use("/posts", postsRouter);

app.use(errorHandler);

app.listen(PORT, () => {
  console.log(`Blog API running at http://localhost:${PORT}`);
});
