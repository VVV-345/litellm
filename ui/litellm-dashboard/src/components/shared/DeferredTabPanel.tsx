import { Activity, type ComponentProps } from "react";
import { TabsContent } from "@/components/ui/tabs";

type Props = ComponentProps<typeof TabsContent> & { active: boolean; visited: boolean };

export function DeferredTabPanel({ active, visited, children, ...props }: Props) {
  return (
    <TabsContent {...props} keepMounted>
      <Activity mode={active ? "visible" : "hidden"}>{active || visited ? children : null}</Activity>
    </TabsContent>
  );
}
