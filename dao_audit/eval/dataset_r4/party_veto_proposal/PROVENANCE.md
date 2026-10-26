# PartyDAO VetoProposal repeated veto (Code4rena 2023-04 H-08) — vulnerable sample (round4)

- repo: https://github.com/PartyDAO/party-protocol
- commit: 34bd3a2d65b157d04a7a16ab860a864bf7faa8bc (parent of the first post-audit VetoProposal change; VetoProposal.sol blob identical to the audited code (code-423n4/2023-04-party @ 440aafac))
- fix commit: 48300a88e06921274fab567b8973f3b597bd9069
- incident / disclosure date: 2023-04-03
- pragma (main file VetoProposal.sol): 0.8.17
- files (dataset name -> in-repo path, byte-identical, verified by git blob hash in BUILD.json):
  - Party.sol -> contracts/party/Party.sol
  - PartyGovernance.sol -> contracts/party/PartyGovernance.sol
  - VetoProposal.sol -> contracts/proposals/VetoProposal.sol
- reports / references:
  - https://code4rena.com/reports/2023-04-party
  - https://github.com/code-423n4/2023-04-party-findings/issues/2
  - https://github.com/PartyDAO/party-protocol/commit/48300a88e06921274fab567b8973f3b597bd9069

## Description

Audit finding (H-08): voteToVeto lacks a hasVoted check, allowing unlimited re-voting.

## Verification notes

Fix commit 48300a8 ('fix(VetoProposal): cannot veto twice') read: adds hasVoted mapping and AlreadyVotedError.
