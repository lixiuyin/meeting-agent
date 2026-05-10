import type { ImageAsset } from "./ImageAssetCard";

export interface PageItem {
  page_num: number;
  text: string;
  heading: string | null;
  image_assets?: ImageAsset[];
}
