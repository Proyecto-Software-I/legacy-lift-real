package cmd

import (
	"testing"

	"github.com/stretchr/testify/require"
)

// TestChannelsFlagAvailableOnRootCmd ensures the flag is still present on the
// root command for interactive mode.
func TestChannelsFlagAvailableOnRootCmd(t *testing.T) {
	t.Parallel()

	flag := rootCmd.Flags().Lookup("channels")
	if flag == nil {
		flag = rootCmd.PersistentFlags().Lookup("channels")
	}
	require.NotNil(t, flag, "the --channels flag must be available on `crush` (rootCmd)")
}
