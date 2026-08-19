package agentresilience.reliability;

import java.util.List;

/** Detects an immediately repeated tool pattern, such as logs,metrics,logs,metrics. */
public final class LoopDetector {
    private final int repeatThreshold;
    public LoopDetector(int repeatThreshold) {
        if (repeatThreshold < 2) throw new IllegalArgumentException("repeatThreshold must be at least 2");
        this.repeatThreshold = repeatThreshold;
    }
    public boolean isLoop(List<String> history) {
        for (int patternLength = 1; patternLength <= history.size() / repeatThreshold; patternLength++) {
            int start = history.size() - patternLength * repeatThreshold;
            boolean repeated = true;
            for (int i = start + patternLength; i < history.size(); i++) {
                if (!history.get(i).equals(history.get(start + ((i - start) % patternLength)))) { repeated = false; break; }
            }
            if (repeated) return true;
        }
        return false;
    }
}
