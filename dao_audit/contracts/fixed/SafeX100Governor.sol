// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface IPastVotes {
    function getPastVotes(address account, uint256 blockNumber) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

interface ISafeTreasury {
    function execute(address target, uint256 value, bytes calldata data) external returns (bytes memory);
}

/// @title SafeX100Governor  (REMEDIATED)
/// @notice Fixes the three governor-side findings:
///   [FIX-1] Council initialization is guarded (one-time, owner-only).
///   [FIX-2] Voting power is read via getPastVotes at a proposal's snapshot block,
///           so flash-loaned balances carry zero weight.
///   [FIX-3] Passed proposals are queued behind a timelock (minDelay) before execution.
contract SafeX100Governor {
    IPastVotes public immutable token;
    ISafeTreasury public treasury;
    address public owner;

    mapping(address => bool) public isCouncil;
    uint256 public councilCount;
    bool public councilInitialized;

    uint256 public immutable minDelay; // timelock, seconds
    uint64 public votingPeriod = 3 days;
    uint256 public quorumBps = 2000;

    struct Proposal {
        address proposer;
        address target;
        uint256 value;
        bytes data;
        uint256 forVotes;
        uint256 againstVotes;
        uint256 snapshotBlock; // weight is measured here
        uint64 voteEnd;
        uint64 eta;            // earliest execution time (set when queued)
        bool queued;
        bool executed;
        mapping(address => bool) voted;
    }

    uint256 public proposalCount;
    mapping(uint256 => Proposal) private _proposals;

    event ProposalCreated(uint256 indexed id, address indexed proposer, uint256 snapshotBlock);
    event Voted(uint256 indexed id, address indexed voter, bool support, uint256 weight);
    event Queued(uint256 indexed id, uint64 eta);
    event Executed(uint256 indexed id);

    constructor(IPastVotes _token, uint256 _minDelay) {
        token = _token;
        minDelay = _minDelay;
        owner = msg.sender;
    }

    function setTreasury(address _treasury) external {
        require(msg.sender == owner, "not owner");
        treasury = ISafeTreasury(_treasury);
    }

    // [FIX-1] one-time, owner-only council bootstrap.
    function initializeCouncil(address[] calldata members) external {
        require(msg.sender == owner, "not owner");
        require(!councilInitialized, "already initialized");
        for (uint256 i = 0; i < members.length; i++) {
            if (!isCouncil[members[i]]) { isCouncil[members[i]] = true; councilCount++; }
        }
        councilInitialized = true;
    }

    function propose(address target, uint256 value, bytes calldata data) external returns (uint256 id) {
        id = ++proposalCount;
        Proposal storage p = _proposals[id];
        p.proposer = msg.sender;
        p.target = target;
        p.value = value;
        p.data = data;
        // [FIX-2] snapshot at the block BEFORE voting opens.
        p.snapshotBlock = block.number - 1;
        p.voteEnd = uint64(block.timestamp) + votingPeriod;
        emit ProposalCreated(id, msg.sender, p.snapshotBlock);
    }

    function castVote(uint256 id, bool support) external {
        Proposal storage p = _proposals[id];
        require(block.timestamp <= p.voteEnd, "voting closed");
        require(!p.voted[msg.sender], "already voted");
        p.voted[msg.sender] = true;
        // [FIX-2] past-block weight: a flash loan taken now has no effect.
        uint256 weight = token.getPastVotes(msg.sender, p.snapshotBlock);
        if (support) p.forVotes += weight; else p.againstVotes += weight;
        emit Voted(id, msg.sender, support, weight);
    }

    // [FIX-3] queue after the vote succeeds; execution waits out the timelock.
    function queue(uint256 id) external {
        Proposal storage p = _proposals[id];
        require(block.timestamp > p.voteEnd, "voting open");
        require(!p.queued, "queued");
        uint256 quorum = (token.totalSupply() * quorumBps) / 10000;
        require(p.forVotes >= quorum, "no quorum");
        require(p.forVotes > p.againstVotes, "rejected");
        p.queued = true;
        p.eta = uint64(block.timestamp + minDelay);
        emit Queued(id, p.eta);
    }

    function execute(uint256 id) external returns (bytes memory ret) {
        Proposal storage p = _proposals[id];
        require(p.queued, "not queued");
        require(!p.executed, "executed");
        require(block.timestamp >= p.eta, "timelock");
        p.executed = true;
        ret = treasury.execute(p.target, p.value, p.data);
        emit Executed(id);
    }

    function proposalInfo(uint256 id)
        external
        view
        returns (uint256 forVotes, uint256 againstVotes, uint256 snapshotBlock, uint64 voteEnd, uint64 eta, bool queued, bool executed)
    {
        Proposal storage p = _proposals[id];
        return (p.forVotes, p.againstVotes, p.snapshotBlock, p.voteEnd, p.eta, p.queued, p.executed);
    }
}
