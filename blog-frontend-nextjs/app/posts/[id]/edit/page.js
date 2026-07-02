import EditPost from "./EditPost";

export function generateStaticParams() {
  return [{ id: "placeholder" }];
}

export default function EditPostPage() {
  return <EditPost />;
}
