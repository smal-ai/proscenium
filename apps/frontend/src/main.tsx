import { createRoot } from "react-dom/client";
import { StudioApp } from "./app/StudioApp";
import { capturePairing, watchPairingHash } from "./app/Pairing";
import { StoryboardBrowser } from "./features/storyboards";
import { ReviewWorkspace, CriticPanel } from "./features/review";
import { Outliner } from "./features/outliner/Outliner";
import { CatalogueBrowser } from "./features/catalogue";
import { AssistantStatus } from "./features/chat";

capturePairing(); // A deliberate full-page visit can replace an existing session.
watchPairingHash(); // Same-tab fragments never replace a stored owner session.
createRoot(document.getElementById("root")!).render(<StudioApp components={{
  assistantStatus: AssistantStatus, review: ReviewWorkspace, outliner: Outliner, catalogue: CatalogueBrowser,
  storyboards: StoryboardBrowser, critic: CriticPanel,
}} />);
