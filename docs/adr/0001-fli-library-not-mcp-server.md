# The daily Check calls the Fli library, not the Fli MCP server

The tool began as "check flights every day using the Fli MCP server", but an MCP server only runs inside an AI session, so a daily Check through it would need every person to have a Claude subscription and would spend tokens on each run. The Check instead calls the Fli Python library from a plain scheduled script, which returns the same Google Flights data for free; the MCP server is used only by the optional Claude Code skill that helps a person write a Watch.

This is also why the tool is self-run rather than hosted: Fli queries Google Flights unofficially, so each person's searches come from their own machine instead of one server that would be rate-limited.
