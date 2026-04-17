import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { $catalogs, $activeCatalogId, type StacCatalog } from "@/stores/catalogStore";
import { CatalogSelector } from "./CatalogSelector";

const mockCatalogs: StacCatalog[] = [
  { id: "cat-1", name: "Local pgSTAC", url: "http://localhost:8082", isDefault: true },
  { id: "cat-2", name: "Production API", url: "https://stac.example.com", isDefault: false },
  { id: "cat-3", name: "Staging API", url: "https://staging.stac.example.com", isDefault: false },
];

function withCatalogs(catalogs: StacCatalog[], activeId: string) {
  return function CatalogDecorator(Story: React.ComponentType) {
    useEffect(() => {
      $catalogs.set(catalogs);
      $activeCatalogId.set(activeId);
      return () => {
        $catalogs.set([]);
        $activeCatalogId.set("");
      };
    }, []);
    return <Story />;
  };
}

const meta: Meta<typeof CatalogSelector> = {
  component: CatalogSelector,
  title: "Catalogs/CatalogSelector",
};

export default meta;
type Story = StoryObj<typeof CatalogSelector>;

export const NoCatalogs: Story = {
  decorators: [withCatalogs([], "")],
};

export const SingleCatalog: Story = {
  decorators: [withCatalogs([mockCatalogs[0]], "cat-1")],
};

export const MultipleCatalogs: Story = {
  decorators: [withCatalogs(mockCatalogs, "cat-1")],
};

export const ThirdCatalogActive: Story = {
  decorators: [withCatalogs(mockCatalogs, "cat-3")],
};
