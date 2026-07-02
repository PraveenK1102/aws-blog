import PostDetail from "./PostDetail";

// Static export needs at least one param. We generate a placeholder.
// All real post IDs are resolved client-side via useParams().
export function generateStaticParams() {
  return [{ id: "placeholder" }];
}

export default function PostDetailPage() {
  return <PostDetail />;
}
