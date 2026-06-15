import { Router } from "express";
import multer from "multer";
import multerS3 from "multer-s3";
import { S3Client } from "@aws-sdk/client-s3";
import { prisma } from "../lib/prisma.js";
import { authMiddleware } from "../middleware/auth.js";

const s3 = new S3Client({ region: process.env.AWS_REGION || "ap-south-1" });
const BUCKET = process.env.S3_UPLOADS_BUCKET || "praveen-blog-uploads";

const upload = multer({
  storage: multerS3({
    s3,
    bucket: BUCKET,
    contentType: multerS3.AUTO_CONTENT_TYPE,
    key: (_req, file, cb) => {
      const safe = Buffer.from(file.originalname, "latin1")
        .toString("utf8")
        .replace(/\s+/g, "-");
      cb(null, `uploads/${Date.now()}-${safe}`);
    },
  }),
  limits: { fileSize: 5 * 1024 * 1024 }, // 5MB
});

export const postsRouter = Router();

// List posts (public)
postsRouter.get("/", async (_req, res, next) => {
  try {
    const posts = await prisma.post.findMany({
      orderBy: { createdAt: "desc" },
      include: { author: { select: { id: true, email: true, name: true } } },
    });
    res.json(posts);
  } catch (e) {
    next(e);
  }
});

// Get one post (public)
postsRouter.get("/:id", async (req, res, next) => {
  try {
    const post = await prisma.post.findUnique({
      where: { id: req.params.id },
      include: { author: { select: { id: true, email: true, name: true } } },
    });
    if (!post) return res.status(404).json({ error: "Post not found" });
    res.json(post);
  } catch (e) {
    next(e);
  }
});

// Create post (auth + optional image)
postsRouter.post("/", authMiddleware, upload.single("image"), async (req, res, next) => {
  try {
    const { title, content } = req.body;
    if (!title?.trim()) return res.status(400).json({ error: "Title required" });
    const imagePath = req.file ? `/${req.file.key}` : null;
    const post = await prisma.post.create({
      data: {
        title: title.trim(),
        content: (content || "").trim(),
        imagePath,
        authorId: req.user.id,
      },
      include: { author: { select: { id: true, email: true, name: true } } },
    });
    res.status(201).json(post);
  } catch (e) {
    next(e);
  }
});

// Update post (auth, author only)
postsRouter.patch("/:id", authMiddleware, upload.single("image"), async (req, res, next) => {
  try {
    const post = await prisma.post.findUnique({ where: { id: req.params.id } });
    if (!post) return res.status(404).json({ error: "Post not found" });
    if (post.authorId !== req.user.id) return res.status(403).json({ error: "Not your post" });
    const { title, content } = req.body;
    const imagePath = req.file ? `/${req.file.key}` : post.imagePath;
    const updated = await prisma.post.update({
      where: { id: req.params.id },
      data: {
        ...(title !== undefined && { title: title.trim() }),
        ...(content !== undefined && { content: content.trim() }),
        imagePath,
      },
      include: { author: { select: { id: true, email: true, name: true } } },
    });
    res.json(updated);
  } catch (e) {
    next(e);
  }
});

// Delete post (auth, author only)
postsRouter.delete("/:id", authMiddleware, async (req, res, next) => {
  try {
    const post = await prisma.post.findUnique({ where: { id: req.params.id } });
    if (!post) return res.status(404).json({ error: "Post not found" });
    if (post.authorId !== req.user.id) return res.status(403).json({ error: "Not your post" });
    await prisma.post.delete({ where: { id: req.params.id } });
    res.status(204).send();
  } catch (e) {
    next(e);
  }
});
