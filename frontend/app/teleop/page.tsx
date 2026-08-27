import { notFound, redirect } from "next/navigation";

import { COLLECTION_ENABLED } from "@/lib/features";

export default function TeleopPage() {
  // Follows /collect: redirecting into a 404 would only hide the reason.
  if (!COLLECTION_ENABLED) notFound();
  redirect("/collect");
}
