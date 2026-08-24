import { RawEpisodeDetail } from "@/components/raw/RawEpisodeDetail";

export default async function RawEpisodePlaceholder({
  params,
}: {
  params: Promise<{ episodeId: string }>;
}) {
  const { episodeId } = await params;
  return <RawEpisodeDetail episodeId={decodeURIComponent(episodeId)} />;
}
