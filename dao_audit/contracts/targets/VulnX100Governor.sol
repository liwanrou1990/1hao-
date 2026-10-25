// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @notice Minimal voting-token view the governor relies on.
interface IVotesLike {
    function balanceOf(address account) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

interface ITreasuryLike {
    function execute(address target, uint256 value, bytes calldata data) external returns (bytes memory);
}

/// @title VulnX100Governor  (RESEARCH TARGET — intentionally vulnerable)
/// @notice Reconstruction of the X100 DAO governor for authorized local-chain
///         security auditing. DO NOT DEPLOY. Each flaw below is annotated with a
///         [VULN-n] tag consumed by the static scanner.
///
/// Demonstrated risk classes:
///   [VULN-1] Governance seat hijacking     — re-initializable council, no guard.
///   [VULN-2] Flash-loan vote manipulation  — voting weight = live balanceOf at vote time.
///   [VULN-3] Missing governance timelock    — passed proposals execute with zero delay.
contract VulnX100Governor {
    IVotesLike public immutable token;
    ITreasuryLike public treasury;

    // --- Council (privileged seats) ---
    mapping(address => bool) public isCouncil;
    uint256 public councilCount;

    // [VULN-1] guard flag exists but is never enforced in initializeCouncil().
    bool public councilInitialized;

    struct Proposal {
        address proposer;
        address target;      // treasury call target
        uint256 value;
        bytes data;
        uint256 forVotes;
        uint256 againstVotes;
        uint64 deadline;     // voting ends
        bool executed;
        mapping(address => bool) voted;
    }

    uint256 public proposalCount;
    mapping(uint256 => Proposal) private _proposals;

    // Governance params
    uint64 public votingPeriod = 1; // seconds — intentionally tiny (see [VULN-3])
    uint256 public quorumBps = 2000; // 20% of total supply

    event CouncilInitialized(address[] members);
    event ProposalCreated(uint256 indexed id, address indexed proposer, address target);
    event Voted(uint256 indexed id, address indexed voter, bool support, uint256 weight);
    event Executed(uint256 indexed id);

    constructor(IVotesLike _token) {
        token = _token;
    }

    function setTreasury(address _treasury) external {
        // Set-once in spirit, but any council member can repoint it.
        require(councilCount == 0 || isCouncil[msg.sender], "not council");
        treasury = ITreasuryLike(_treasury);
    }

    // =========================================================================
    // [VULN-1] GOVERNANCE SEAT HIJACKING
    // Anyone can (re)initialize the council at any time. The `councilInitialized`
    // flag is written but never checked, so an attacker calls this with only
    // their own address and becomes the sole council member.
    // =========================================================================
    function initializeCouncil(address[] calldata members) external {
        // BUG: missing `require(!councilInitialized, "init")`.
        // Overwrites any prior council membership bookkeeping.
        for (uint256 i = 0; i < members.length; i++) {
            if (!isCouncil[members[i]]) {
                isCouncil[members[i]] = true;
                councilCount++;
            }
        }
        councilInitialized = true;
        emit CouncilInitialized(members);
    }

    function propose(address target, uint256 value, bytes calldata data) external returns (uint256 id) {
        id = ++proposalCount;
        Proposal storage p = _proposals[id];
        p.proposer = msg.sender;
        p.target = target;
        p.value = value;
        p.data = data;
        p.deadline = uint64(block.timestamp) + votingPeriod;
        emit ProposalCreated(id, msg.sender, target);
    }

    // =========================================================================
    // [VULN-2] FLASH-LOAN VOTE MANIPULATION
    // Voting weight is read from the LIVE token balance at the instant of voting.
    // There is no snapshot taken at proposal creation, so an attacker can borrow
    // a huge balance via flash loan, vote, and repay within one transaction.
    // =========================================================================
    function castVote(uint256 id, bool support) external {
        Proposal storage p = _proposals[id];
        require(block.timestamp <= p.deadline, "voting closed");
        require(!p.voted[msg.sender], "already voted");
        p.voted[msg.sender] = true;

        uint256 weight = token.balanceOf(msg.sender); // BUG: live balance, no snapshot
        if (support) p.forVotes += weight;
        else p.againstVotes += weight;
        emit Voted(id, msg.sender, support, weight);
    }

    // =========================================================================
    // [VULN-3] MISSING GOVERNANCE TIMELOCK
    // Once the (tiny) voting window closes and quorum is met, execution runs
    // immediately. There is no queue/delay, so token holders and guardians have
    // no window to react, exit, or veto a malicious proposal.
    // =========================================================================
    function execute(uint256 id) external returns (bytes memory ret) {
        Proposal storage p = _proposals[id];
        require(!p.executed, "executed");
        require(block.timestamp > p.deadline, "voting open");

        uint256 quorum = (token.totalSupply() * quorumBps) / 10000;
        require(p.forVotes >= quorum, "no quorum");
        require(p.forVotes > p.againstVotes, "rejected");

        p.executed = true;
        // BUG: no timelock delay between approval and execution.
        ret = treasury.execute(p.target, p.value, p.data);
        emit Executed(id);
    }

    // --- views ---
    function proposalInfo(uint256 id)
        external
        view
        returns (address proposer, address target, uint256 forVotes, uint256 againstVotes, uint64 deadline, bool executed)
    {
        Proposal storage p = _proposals[id];
        return (p.proposer, p.target, p.forVotes, p.againstVotes, p.deadline, p.executed);
    }
}
