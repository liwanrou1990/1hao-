// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// A third-party-style governance contract using DIFFERENT naming conventions
// than the bundled case study, to exercise the analyzer on unfamiliar code.
interface IVoteToken {
    function balanceOf(address) external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

interface IReservePair {
    function getReserves() external view returns (uint112, uint112);
}

contract MiniDAO {
    IVoteToken public voteToken;
    mapping(address => bool) public stewards;
    uint256 public stewardCount;

    struct Ballot { address target; bytes payload; uint256 yes; uint64 closes; bool done; }
    mapping(uint256 => Ballot) public ballots;
    uint256 public ballotCount;
    uint256 public votingWindow = 300;

    constructor(IVoteToken t) { voteToken = t; }

    // Privileged bootstrap with no guard / no owner check.
    function configureStewards(address[] calldata who) external {
        for (uint256 i; i < who.length; i++) {
            stewards[who[i]] = true;
            stewardCount++;
        }
    }

    function submit(address target, bytes calldata payload) external returns (uint256 id) {
        id = ++ballotCount;
        ballots[id] = Ballot(target, payload, 0, uint64(block.timestamp) + uint64(votingWindow), false);
    }

    // Live-balance weighting, no snapshot.
    function castBallot(uint256 id, bool yes) external {
        require(block.timestamp <= ballots[id].closes, "closed");
        uint256 w = voteToken.balanceOf(msg.sender);
        if (yes) ballots[id].yes += w;
    }

    // Immediate execution with arbitrary call, no timelock, no allowlist.
    function enact(uint256 id) external returns (bytes memory) {
        Ballot storage b = ballots[id];
        require(block.timestamp > b.closes, "open");
        require(b.yes >= voteToken.totalSupply() / 5, "quorum");
        b.done = true;
        (bool ok, bytes memory ret) = b.target.call(b.payload);
        require(ok, "fail");
        return ret;
    }
}

contract LendingDesk {
    IReservePair public pair;
    mapping(address => uint256) public deposits;
    constructor(IReservePair p) { pair = p; }
    function deposit() external payable { deposits[msg.sender] += msg.value; }
    // Values collateral from instantaneous reserves.
    function collateralValue(address u) public view returns (uint256) {
        (uint112 r0, uint112 r1) = pair.getReserves();
        uint256 price = (uint256(r1) * 1e18) / uint256(r0);
        return (deposits[u] * price) / 1e18;
    }
}
