import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect, useState } from "react";
import { $catalogs, type StacCatalog } from "@/stores/catalogStore";
import { CatalogSelector } from "./CatalogSelector";

const mockCatalogs: StacCatalog[] = [
  { id: "cat-1", name: "Local pgSTAC", url: "http://localhost:8082", isDefault: true },
  { id: "cat-2", name: "Production API", url: "https://stac.example.com", isDefault: false },
  { id: "cat-3", name: "Staging API", url: "https://staging.stac.example.com", isDefault: false },
];

function withCatalogs(catalogs: StacCatalog[]) {
  return function CatalogDecorator(Story: React.ComponentType) {
    useEffect(() => {
      $catalogs.set(catalogs);
      return () => {
        $catalogs.set([]);
      };
    }, []);
    return <Story />;
  };
}

/**
 * The selector is controlled (UI-10) — the owning surface holds the selection,
 * so the stories hold it too.
 */
function ControlledSelector({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial);
  return <CatalogSelector value={value} onChange={setValue} />;
}

const meta: Meta<typeof CatalogSelector> = {
  component: CatalogSelector,
  title: "Catalogs/CatalogSelector",
};

export default meta;
type Story = StoryObj<typeof CatalogSelector>;

export const NoCatalogs: Story = {
  decorators: [withCatalogs([])],
  render: () => <ControlledSelector initial="" />,
};

export const SingleCatalog: Story = {
  decorators: [withCatalogs([mockCatalogs[0]])],
  render: () => <ControlledSelector initial="cat-1" />,
};

export const MultipleCatalogs: Story = {
  decorators: [withCatalogs(mockCatalogs)],
  render: () => <ControlledSelector initial="cat-1" />,
};

export const ThirdCatalogSelected: Story = {
  decorators: [withCatalogs(mockCatalogs)],
  render: () => <ControlledSelector initial="cat-3" />,
};
